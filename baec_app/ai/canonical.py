"""baec-canonical-json/v1: the one canonical JSON form used for every Phase 6 digest.

Rules: object keys sorted by code point at every depth; compact separators;
UTF-8 with non-ASCII written directly (ensure_ascii=False); strings left exactly as
given (no Unicode normalization); finite numbers only; true, false, and null as
literals. Only dict (with str keys), list, tuple, str, int, finite float, bool and
None are accepted. Anything else is refused: nothing is ever converted with str()
or repr().
"""

from __future__ import annotations

import hashlib
import json
import math

CANONICALIZATION_VERSION = "baec-canonical-json/v1"


class CanonicalizationError(ValueError):
    """A value cannot be represented in baec-canonical-json/v1."""


def _check(value: object) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise CanonicalizationError("only finite numbers are canonical")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise CanonicalizationError("object keys must be strings")
            _check(item)
        return
    if type(value) in (list, tuple):
        for item in value:
            _check(item)
        return
    raise CanonicalizationError(f"{type(value).__name__} is not a canonical JSON value")


def canonical_json(value: object) -> str:
    """The canonical JSON text of value."""
    _check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_text(text: str) -> str:
    """Lowercase hexadecimal SHA-256 of the exact UTF-8 bytes of text."""
    if type(text) is not str:
        raise CanonicalizationError("only text can be digested")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_digest(value: object) -> str:
    """SHA-256 of the canonical JSON of value."""
    return sha256_text(canonical_json(value))
