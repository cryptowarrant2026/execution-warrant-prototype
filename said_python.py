# src/said_python.py
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict

from blake3 import blake3

PLACEHOLDER_LEN = 44
PLACEHOLDER_CHAR = "#"
DERIVATION_CODE = "E"  # CESR derivation code for Blake3-256

# CESR/QB64 Base64URL alphabet
_B64URL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _qb64_blake3_256(digest: bytes) -> str:
    """
    CESR/QB64 encoding for derivation code 'E' (Blake3-256).

    CESR layout for 256-bit digests (43 chars tail):
      - Take the 256 digest bits
      - PREPEND 2 zero bits on the LEFT => 258-bit stream
      - Emit 43 Base64URL sextets (43*6 = 258 bits)

    Full SAID is:
      'E' + <43-char tail>

    This is why it is NOT equal to: 'E' + base64url_no_pad(digest)
    """
    if len(digest) != 32:
        raise ValueError("digest must be 32 bytes (256-bit).")

    # Interpret digest as 256-bit integer
    dig_val = int.from_bytes(digest, "big")

    # We don't materialize the 258-bit stream [00][digest_bits]; instead we extract sextets directly:
    # - Sextet 0 is (00 || top4(digest)), so idx ∈ [0..15] (alphabet[0..15] = 'A'..'P').
    # - Sextets 1..42 are successive 6-bit groups from the remaining digest bits.

    out = []
    for s in range(43):
        shift = 252 - (6 * s)  # 252, 246, 240, ..., 0
        idx = (dig_val >> shift) & 0x3F
        out.append(_B64URL_ALPHABET[idx])

    return DERIVATION_CODE + "".join(out)


def python_derivation_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the derivation object:
      - 'd' first with 44 '#'
      - preserve insertion order of remaining keys
      - ignore any input 'd'
    """
    if not isinstance(obj, dict):
        raise TypeError("python_derivation_obj expects a dict (JSON object).")

    body = deepcopy(obj)

    sad: Dict[str, Any] = {"d": PLACEHOLDER_CHAR * PLACEHOLDER_LEN}
    for k, v in body.items():
        if k == "d":
            continue
        sad[k] = v
    return sad


def python_derivation_json(obj: Dict[str, Any]) -> str:
    """
    Exact JSON string (no whitespace) whose UTF-8 bytes are hashed.
    """
    sad = python_derivation_obj(obj)
    return json.dumps(sad, separators=(",", ":"), ensure_ascii=False)


def python_said_generate(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    SAID generation aligned to Rust/HCF for Blake3_256 ('E'):
      1) build derivation JSON with '#' placeholder
      2) Blake3-256 digest
      3) CESR/QB64 encode using the LEFT-PAD-2 rule
      4) embed 'd'
    """
    deriv_json = python_derivation_json(obj)
    digest = blake3(deriv_json.encode("utf-8")).digest()

    said = _qb64_blake3_256(digest)

    out = json.loads(deriv_json)
    out["d"] = said
    return out


def embed_said(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Canonical SAID embedder for any RBC object using the `d` field.
    """
    return python_said_generate(obj)

