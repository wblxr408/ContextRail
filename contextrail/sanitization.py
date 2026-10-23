"""Versioned capture boundary. MIT upstream patterns with local JSON/role adapters.

The shadow-span algorithm and credential patterns are ported from ai-memory
1bd866375b01031d63db54862a133376598d3701 (Fabio Akita, MIT).
See THIRD_PARTY_NOTICES.md. This is a bounded known-pattern scrubber, not a
detector of arbitrary opaque or encoded secrets.
"""

from dataclasses import dataclass
import json
import math
import re

from ._vendor.ai_memory_patterns import BUILTIN_PATTERNS


POLICY = 'rail-capture-v2-ai-memory-1bd8663'
REDACTED = '[REDACTED]'
ROLES = frozenset({'user', 'assistant', 'tool', 'document', 'lifecycle', 'error', 'evidence', 'memory'})
_TOKEN = object()
_KEY = re.compile(r'authorization|authentication|password|passwd|(?:^|[._-])pwd(?:$|[._-])|'
                  r'api[-_]?key|access[-_]?token|refresh[-_]?token|auth[-_]?token|'
                  r'secret|credential|cookie|signature|private[-_]?key|bearer', re.I)
_ASSIGN = re.compile(r'''(?ix)(?P<label>\b(?:[\w.-]*(?:password|passwd|secret|credential|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token)|[A-Z][A-Z0-9_]*_(?:KEY|TOKEN|KEY_ID|PASSPHRASE|PEPPER|SALT)))
    ["']?\s*[:=]\s*(?:"(?P<double>[^"\r\n]*)"|'(?P<single>[^'\r\n]*)'|(?P<bare>[^\s,;"'{}\[\]]+))''')
_ESCAPES = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]')
# ai-memory's \S+ env rules consume JSON delimiters and redact placeholders on
# retry. Replace only those rules with _ASSIGN's bounded value captures.
def _python_pattern(pattern, label):
    # Rust regex is linear-time; Python re backtracks. Avoid retrying an
    # unbounded scheme at every letter of a long word, and bound auth-header
    # name prefixes whose nested repetitions otherwise become quadratic.
    if label == 'url_credentials':
        pattern = r'(?<![A-Za-z0-9+.-])' + pattern
    if label == 'auth_header':
        pattern = pattern.replace('[A-Za-z0-9_-]*', '[A-Za-z0-9_-]{0,128}').replace('[a-z0-9]*', '[a-z0-9]{0,128}')
    return re.compile(pattern)


_PATTERNS = tuple((_python_pattern(p, label), label) for p, label in BUILTIN_PATTERNS if label != 'env_secret')


class CaptureError(ValueError):
    """Fixed, input-free rejection suitable for logs and error envelopes."""


@dataclass(frozen=True, init=False)
class ProcessedCapture:
    payload: str
    role: str
    policy: str

    def __init__(self, payload, role, *, _token=None):
        if _token is not _TOKEN:
            raise CaptureError('processed_capture_requires_sanitizer')
        object.__setattr__(self, 'payload', payload)
        object.__setattr__(self, 'role', role)
        object.__setattr__(self, 'policy', POLICY)

    @property
    def value(self):
        return json.loads(self.payload)

    def provenance(self):
        return {'policy': self.policy, 'role': self.role, 'state': 'processed',
                'range_basis': 'processed_value_only'}


def _ignorable(c):
    n = ord(c)
    return n in {0xAD, 0x34F, 0x61C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFEFF, 0xFFA0} or any(
        a <= n <= b for a, b in ((0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
        (0x2060, 0x206F), (0xFE00, 0xFE0F), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
        (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF)))


def _visible(text):
    return ''.join(c for c in text if not _ignorable(c))


def _controls(text):
    text = _ESCAPES.sub('', text)
    return ''.join(c for c in text if not (ord(c) < 32 and c not in '\t\r\n'
        or 0x7F <= ord(c) <= 0x9F or 0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069))


def _replace(text, pattern, label, shadow=False):
    # Character-index mapping is intentional: UTF-8 offsets are assigned only
    # when the processed artifact is encoded, never copied from raw evidence.
    indexes = [i for i, c in enumerate(text) if not _ignorable(c)] if shadow else None
    view = ''.join(text[i] for i in indexes) if shadow else text
    spans = []
    for match in pattern.finditer(view):
        start, end = match.span('secret') if 'secret' in pattern.groupindex else match.span()
        if start == end:
            continue
        if shadow:
            start, end = indexes[start], indexes[end - 1] + 1
        spans.append((start, end))
    for start, end in reversed(spans):
        text = text[:start] + '[REDACTED:' + label + ']' + text[end:]
    return text


def _assignment(match):
    value = next((match[g] for g in ('double', 'single', 'bare') if match[g] is not None), '')
    return match[0] if value.startswith('[REDACTED') else match['label'] + '=' + REDACTED


def _scrub(text, secrets):
    text = _controls(text)
    # Recognized values echoed elsewhere in the same capture (e.g. claims and
    # source quotes) must be processed consistently before quote validation.
    for secret in sorted(secrets, key=len, reverse=True):
        if len(secret) >= 6:
            text = text.replace(secret, REDACTED)
        elif text == secret:
            text = REDACTED
    has_ignorables = any(_ignorable(c) for c in text)
    for pattern, label in _PATTERNS:
        if has_ignorables:
            text = _replace(text, pattern, label, True)
        text = _replace(text, pattern, label)
    # Also recognize assignments split by invisible formatting characters.
    visible = _visible(text)
    for match in _ASSIGN.finditer(visible):
        start, end = match.span()
        if _assignment(match) == match[0]:
            continue
        indexes = [i for i, c in enumerate(text) if not _ignorable(c)]
        a, b = indexes[start], indexes[end - 1] + 1
        return _scrub(text[:a] + _assignment(match) + text[b:], secrets)
    return text


def sanitize_capture(value, role='document'):
    if role not in ROLES:
        raise CaptureError('unsupported_capture_role')
    if isinstance(value, ProcessedCapture):
        if value.policy != POLICY or value.role != role:
            raise CaptureError('processed_capture_policy_or_role_mismatch')
        return value
    secrets, ancestors = set(), set()
    remaining = [20000, 4 * 1024 * 1024]

    def visit(item, depth=0, collect=True):
        remaining[0] -= 1
        if depth > 40 or remaining[0] < 0:
            raise CaptureError('capture_structure_limit')
        if isinstance(item, str):
            remaining[1] -= len(item.encode('utf-8'))
            if remaining[1] < 0:
                raise CaptureError('capture_text_limit')
            # A serialized document still has sensitive keys. Decode only a
            # complete JSON value; prose/malformed JSON uses text rules.
            if item.lstrip().startswith(('{', '[')):
                try:
                    parsed = json.loads(item)
                except (ValueError, RecursionError):
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    result = visit(parsed, depth + 1, collect)
                    return item if collect else json.dumps(result, ensure_ascii=False, separators=(',', ':'))
            if collect:
                for match in _ASSIGN.finditer(_visible(_controls(item))):
                    for group in ('double', 'single', 'bare'):
                        if match[group] and not match[group].startswith('[REDACTED'):
                            secrets.add(match[group])
                return item
            return _scrub(item, secrets)
        if item is None or type(item) in (bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        if not isinstance(item, (dict, list)):
            raise CaptureError('unsupported_capture_value')
        if id(item) in ancestors:
            raise CaptureError('cyclic_capture_value')
        ancestors.add(id(item))
        try:
            if isinstance(item, list):
                out, hide = [], False
                for child in item:
                    if hide:
                        if collect:
                            learn(child)
                        out.append(REDACTED)
                        hide = False
                    else:
                        out.append(visit(child, depth + 1, collect))
                        hide = isinstance(child, str) and child.startswith('-') and '=' not in child and bool(_KEY.search(_visible(child)))
                return out
            out = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise CaptureError('capture_keys_must_be_text')
                visit(key, depth + 1, collect)
                if _KEY.search(_visible(key)):
                    if collect:
                        learn(child)
                    out[_scrub(key, secrets) if not collect else key] = REDACTED
                else:
                    out[_scrub(key, secrets) if not collect else key] = visit(child, depth + 1, collect)
            return out
        finally:
            ancestors.remove(id(item))

    def learn(item):
        # Still validate secret-shaped subtrees: cycles or unsupported values
        # cannot bypass input limits merely by hiding under a sensitive key.
        visit(item, 1, True)
        if isinstance(item, str) and item and not item.startswith('[REDACTED'):
            secrets.add(item)

    try:
        visit(value)
        remaining[:] = [20000, 4 * 1024 * 1024]
        result = visit(value, collect=False)
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        return ProcessedCapture(payload, role, _token=_TOKEN)
    except CaptureError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CaptureError('invalid_capture_value') from None


def clean_text(text, limit=None):
    if not isinstance(text, str):
        raise CaptureError('capture_text_required')
    result = sanitize_capture(text, 'error').value
    return result if limit is None else result[:limit]


def require_safe_identity(value):
    if not isinstance(value, str) or clean_text(value) != value:
        raise CaptureError('credential_in_capture_identity')
    return value
