"""
Input validators and HMAC helpers for PlanB security hardening.
"""

import hmac
import re
from hashlib import sha256

_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_S3_SAFE_RE = re.compile(r"[^A-Za-z0-9+\-_]")


def validate_phone(phone: str) -> str:
    r"""Assert E.164 format and return sanitised phone string.

    Raises ValueError if the phone does not match ^\+[1-9]\d{7,14}$.
    """
    if not _PHONE_RE.match(phone or ""):
        raise ValueError(f"Invalid phone number format: {phone!r}")
    return phone


def sanitize_s3_key_segment(segment: str) -> str:
    """Strip path traversal and unsafe chars from an S3 key segment.

    Keeps only [A-Za-z0-9+\\-_]. Raises ValueError if the result is empty.
    """
    safe = _S3_SAFE_RE.sub("", segment or "")
    if not safe:
        raise ValueError(f"S3 key segment is empty after sanitisation: {segment!r}")
    return safe


def generate_oauth_state(phone: str, secret: str) -> str:
    """Return a signed state token: '{phone}:{hmac_hex}'."""
    sig = hmac.new(secret.encode(), phone.encode(), sha256).hexdigest()
    return f"{phone}:{sig}"


def verify_oauth_state(state_param: str, secret: str) -> str:
    """Verify a signed OAuth state token and return the embedded phone number.

    Raises ValueError if the signature is invalid or the format is wrong.
    """
    if not state_param or ":" not in state_param:
        raise ValueError("OAuth state param is missing or malformed")
    phone, _, received_sig = state_param.partition(":")
    expected_sig = hmac.new(secret.encode(), phone.encode(), sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, received_sig):
        raise ValueError("OAuth state HMAC verification failed")
    return phone


def verify_meta_signature(body: bytes, header: str, secret: str) -> bool:
    """Verify the X-Hub-Signature-256 header from Meta webhooks.

    Returns True if the HMAC-SHA256(secret, body) matches the header value.
    Returns False on mismatch or parsing errors.
    """
    if not header or not header.startswith("sha256="):
        return False
    received = header[len("sha256="):]
    expected = hmac.new(secret.encode(), body, sha256).hexdigest()
    return hmac.compare_digest(expected, received)
