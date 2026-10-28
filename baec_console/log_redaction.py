"""Credential redaction for every log record a production process writes. (V0.4 Phase 5; IMPLEMENTATION.)

BAEC's own code never logs a URL, password, token, cookie, or buyer statement: its errors are fixed messages or name
settings only. The libraries under it (Streamlit, Starlette, uvicorn, Authlib, psycopg and its pool, the Claude SDK)
log on their own, and a library error can quote what it was given. install() makes every log record in the process,
from any logger, created now or later, pass through redact() before any handler sees it: the message, its arguments,
an exception's traceback text, and stack information.

What is replaced by "[redacted]": the user-info of any URL (scheme://user:password@ -> scheme://[redacted]@);
password, client_secret, cookie_secret, and token values in key=value or key: value form; OAuth code, state, nonce,
and token query parameters; Authorization bearer values; JSON Web Tokens; Streamlit's cookie values; and Claude-style
secret keys. Redaction is a last line of defence, not a licence to log secrets: it cannot recognize a secret with no
recognizable shape.
"""

from __future__ import annotations

import logging
import re
import threading

REDACTED = "[redacted]"

_PATTERNS = (
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+@"), r"\1" + REDACTED + "@"),
    (re.compile(r"(?i)\b(password|passwd|client_secret|cookie_secret|secret|access_token|refresh_token|id_token)"
                r"(\"?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s&;,]+)"), r"\1\2" + REDACTED),
    (re.compile(r"(?i)([?&](?:code|state|nonce|id_token|access_token|refresh_token)=)[^&#\s]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), r"\1 " + REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), REDACTED),
    (re.compile(r"\b(_streamlit_[a-z_]+?(?:_\d+)?)=[^;\s]+"), r"\1=" + REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9]{2,}-[A-Za-z0-9_-]{16,}"), REDACTED),
)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


_INSTALLED = False
_LOCK = threading.Lock()


def install() -> None:
    """Redact every log record created from now on, in this process. Idempotent."""
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        create = logging.getLogRecordFactory()
        formatter = logging.Formatter()

        def redacting_record(*args, **kwargs) -> logging.LogRecord:
            record = create(*args, **kwargs)
            try:
                record.msg, record.args = redact(record.getMessage()), ()
            except Exception:  # a record whose arguments do not format: keep nothing of it
                record.msg, record.args = "[unformattable log message]", ()
            if record.exc_info:
                record.exc_text = redact(formatter.formatException(record.exc_info))
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
            return record

        logging.setLogRecordFactory(redacting_record)
        _INSTALLED = True
