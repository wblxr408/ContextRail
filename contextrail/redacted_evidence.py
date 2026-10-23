"""Trusted-host projection: precise raw evidence retains its separate identity."""

from dataclasses import asdict
from uuid import uuid4

from .errors import InvalidRequest
from .models import Ref, canonical
from .sanitization import CaptureError, sanitize_capture


def redact_evidence(store, scope, lease, source, role):
    if not isinstance(source, Ref):
        raise InvalidRequest('An exact source reference is required.')
    # Read and publication share a lease/freeze transaction. The input cannot
    # disappear or change between its validation and the derived manifest.
    with store.transaction():
        store._lease(scope, lease)
        original = store.get(scope, source)
        if len(original.content) > 4 * 1024 * 1024:
            raise InvalidRequest('Capture projection input exceeds 4 MiB.')
        try:
            processed = sanitize_capture(original.content.decode('utf-8'), role)
            body = processed.value.encode('utf-8')
        except UnicodeError:
            raise InvalidRequest('Capture projection requires UTF-8 source bytes.') from None
        except CaptureError as exc:
            raise InvalidRequest(str(exc)) from None
        prefix = 'redacted/' + uuid4().hex
        body_ref = Ref(prefix + '/body', 1)
        manifest = {'schema': 'contextrail.redacted-evidence.v1', 'source_ref': asdict(source),
                    'processed_ref': asdict(body_ref), 'capture': processed.provenance(),
                    'range_unit': 'utf8_bytes', 'range_basis': 'each_ref_own_revision',
                    'source_bytes': len(original.content), 'processed_bytes': len(body),
                    'trust': 'untrusted_source_data', 'offset_mapping': 'not_position_preserving'}
        # _put is safe here: this transaction already holds the same owner and
        # freeze fencing as put_many, without introducing a nested transaction.
        store._put(scope, body_ref.name, body, expected_revision=0)
        manifest_ref = store._put(scope, prefix + '/manifest', canonical(manifest).encode(),
                                  expected_revision=0, media_type='application/json')
        return {**manifest, 'manifest_ref': asdict(manifest_ref)}
