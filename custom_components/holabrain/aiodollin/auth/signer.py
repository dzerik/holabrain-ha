"""Request signing for the HolaBrain (dollin) cloud API.

Two independent signature schemes are used by the cloud:

* **OEM signature** — legacy ``/v1/*`` endpoints. The ``sign`` header is
  ``hex(HMAC_SHA256(app_key + body + random, app_secret))``.
* **ToB v2.0 signature** — ``/midea/open/business/v1/*`` endpoints. The ``Signature``
  header is ``base64(HMAC_SHA256(client_secret, method + path + body))`` and is sent
  together with ``ClientId`` and ``SignatureVersion: 2.0``.

Every function here is pure: inputs are passed explicitly, there is no global state and no
network access, so the signing behaviour can be pinned by unit tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

__all__ = ["encrypt_password", "oem_sign", "tob_sign"]


def oem_sign(app_key: str, app_secret: str, body: str, random: str) -> str:
    """Return the lowercase-hex OEM ``sign`` value for a request body.

    The three parts are concatenated *without* a separator, matching the cloud, so callers
    must pass the exact serialized ``body`` that will be sent on the wire.
    """
    message = f"{app_key}{body}{random}".encode()
    return hmac.new(app_secret.encode(), message, hashlib.sha256).hexdigest().lower()


def tob_sign(client_secret: str, method: str, path: str, body: str) -> str:
    """Return the base64 ToB v2.0 ``Signature`` value for a request.

    ``method``, ``path`` and ``body`` are concatenated without a separator. ``body`` is
    signed as UTF-8 bytes exactly as serialized — do not ASCII-escape non-Latin content,
    or the signature will not match a body containing named entities.
    """
    message = f"{method}{path}{body}".encode()
    digest = hmac.new(client_secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def encrypt_password(encrypt_key: str, plain: str) -> str:
    """Encrypt an account password for the login endpoint.

    ``AES-128-CBC`` (PKCS7 padding) over the lowercase-hex SHA-256 of the plaintext. The
    key and IV are the first and second 16 hex characters of ``SHA-256(encrypt_key)``.
    Deterministic (fixed IV), which the login endpoint expects.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    inner = _sha256_hex(plain).encode()
    material = _sha256_hex(encrypt_key)
    key = material[0:16].encode()
    iv = material[16:32].encode()

    padder = PKCS7(128).padder()
    padded = padder.update(inner) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return (encryptor.update(padded) + encryptor.finalize()).hex().lower()


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest().lower()
