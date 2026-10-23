"""Development adapter. Deployment emits the same canonical implementation standalone."""
from contextrail.sanitization import POLICY, CaptureError, clean_text, require_safe_identity, sanitize_capture
