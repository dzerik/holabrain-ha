"""Pairing primitives.

Two values are needed to claim an appliance that has announced itself to the cloud:

* the **serial number**, which the cloud accepts only encrypted with a key derived from the
  caller's own session — so a captured request cannot be replayed by anyone else; and
* the **verification code**, which is not a device secret at all: it is derived from the
  Wi-Fi network the appliance was joined to.

Both functions here are pure and free of network access, so their wire format can be pinned
by tests rather than discovered at runtime.
"""

from __future__ import annotations

import hashlib
import os

__all__ = ["derive_verification_code", "encrypt_serial"]

_SN_KEY_BYTES = 16
_IV_BYTES = 16
_VERIFICATION_BYTES = 16
_NONCE_BYTES = 2


def encrypt_serial(serial: str, access_token: str, *, iv: bytes | None = None) -> str:
    """Encrypt an appliance serial for the verification endpoint.

    AES-CBC with PKCS7 padding. The key is the first 16 **raw** bytes of ``SHA-256`` over the
    access token — not its hex form, which decrypts to garbage and is rejected. The random IV
    is prepended to the ciphertext, both hex-encoded, because the cloud has no other way to
    know it.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    key = hashlib.sha256(access_token.encode()).digest()[:_SN_KEY_BYTES]
    nonce = os.urandom(_IV_BYTES) if iv is None else iv
    padder = PKCS7(128).padder()
    padded = padder.update(serial.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(nonce)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return nonce.hex() + ciphertext.hex()


def derive_verification_code(
    bssid: str, wifi_password: str, *, nonce: bytes | None = None
) -> str:
    """Derive the code an appliance reports after joining a Wi-Fi network.

    Two random bytes followed by the first 14 bytes of ``MD5(bssid || password)``. The
    appliance is told this value while it is being set up and reports it back to the cloud,
    which is what ties "this appliance" to "whoever knows this network".

    ``bssid`` is accepted in any common notation (``aa:bb:cc:dd:ee:ff``, ``AA-BB-…``, plain
    hex); only its twelve hex digits matter.
    """
    raw = bytes.fromhex("".join(ch for ch in bssid if ch in "0123456789abcdefABCDEF"))
    digest = hashlib.md5(raw + wifi_password.encode()).digest()
    prefix = os.urandom(_NONCE_BYTES) if nonce is None else nonce
    return (prefix + digest)[:_VERIFICATION_BYTES].hex()
