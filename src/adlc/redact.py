"""Remove secrets from text before it is written into a note a person may share (FR-16, FR-18b).

This is the small, conservative pass the handoff note needs. The full scrubbing of prompts and
logs (ADLC-027) can build on it.
"""

import re

REDACTED = "[REDACTED]"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S
)
_TOKENS = re.compile(
    r"\bsk-[A-Za-z0-9_-]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNED = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key)[A-Za-z0-9_.-]*)"
    r"(\s*[=:]\s*)(?!\[REDACTED\])[^\s\"',;]+"
)
_URL_LOGIN = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")


def redact(text: str) -> str:
    text = _PRIVATE_KEY.sub(REDACTED, text)
    text = _TOKENS.sub(REDACTED, text)
    text = _BEARER.sub(rf"\1 {REDACTED}", text)
    text = _ASSIGNED.sub(rf"\1\2{REDACTED}", text)
    return _URL_LOGIN.sub(rf"\1{REDACTED}@", text)
