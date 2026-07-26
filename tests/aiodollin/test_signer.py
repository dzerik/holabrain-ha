"""Signing tests.

These pin the exact wire behaviour of the two cloud signature schemes. The known-answer
values are computed from the real algorithm; if a refactor changes concatenation order,
encoding, or hashing, these fail loudly — which is the point, because a wrong signature
fails silently at runtime with an opaque server error.
"""

from custom_components.holabrain.aiodollin.auth.signer import (
    encrypt_password,
    oem_sign,
    tob_sign,
)

CLIENT_SECRET = "6dff9cfcd9366967bf451ebaa7e45a7d"
APP_KEY = "meicloud"
APP_SECRET = "PROD_VnoClJI9aikS8dyy"
ENCRYPT_KEY = "4dbc9ff6c15944d78eebb581c2b23de3"

_CMD_PATH = "/midea/open/business/v1/device/command/100000000000001"


def test_tob_sign_known_answer():
    body = '{"instruction":{"runState":"1"}}'
    assert (
        tob_sign(CLIENT_SECRET, "POST", _CMD_PATH, body)
        == "zMa3Ij/4mjGOTAgwJRF7Br47H+UJ22EdEUsDfJdmh4A="
    )


def test_tob_sign_non_ascii_body_signed_as_utf8():
    # Awkward-but-real: a command carrying a Cyrillic value. The signature must be over the
    # raw UTF-8 bytes of the serialized body. A JSON serializer left at the default
    # ensure_ascii=True would produce "\\u041f..." and a signature that the cloud rejects,
    # so this guards the whole named-value control path (e.g. renaming an appliance).
    body = '{"instruction":{"name":"Посудомоечная"}}'
    assert (
        tob_sign(CLIENT_SECRET, "POST", _CMD_PATH, body)
        == "cpZMYrZAnmQ532/nfW3fW9CWsMT7lIuLZPwxrsmvmuc="
    )


def test_oem_sign_known_answer():
    assert (
        oem_sign(APP_KEY, APP_SECRET, '{"query":"1"}', "1785026964358")
        == "a7f23cb5c28483e2b5d36e038a3bf7d9ae4e5b12fe9f0cb511b975ca228e4353"
    )


def test_tob_sign_empty_object_is_not_empty_string():
    # HA frequently issues empty-payload requests. The body must be the literal "{}" the
    # transport puts on the wire, which is distinct from an empty string.
    signed_obj = tob_sign(CLIENT_SECRET, "POST", "/p", "{}")
    signed_empty = tob_sign(CLIENT_SECRET, "POST", "/p", "")
    assert signed_obj != signed_empty


def test_tob_sign_concatenation_has_no_delimiter():
    # The scheme concatenates method+path+body with no separator, so equal concatenations
    # collide. This is a property of the cloud protocol, not a defect: the signer must
    # reproduce it exactly and must never "helpfully" insert a delimiter.
    assert tob_sign(CLIENT_SECRET, "POST", "/ab", "cd") == tob_sign(
        CLIENT_SECRET, "POST", "/a", "bcd"
    )


def test_oem_sign_random_changes_signature():
    # `random` is a nonce; two otherwise-identical requests must sign differently.
    a = oem_sign(APP_KEY, APP_SECRET, "{}", "1")
    b = oem_sign(APP_KEY, APP_SECRET, "{}", "2")
    assert a != b


def test_encrypt_password_deterministic_and_hex():
    out = encrypt_password(ENCRYPT_KEY, "s3cr3t-Pa$$")
    # Fixed IV → deterministic, which the login endpoint relies on.
    assert out == encrypt_password(ENCRYPT_KEY, "s3cr3t-Pa$$")
    assert out and all(c in "0123456789abcdef" for c in out)
    # Ciphertext of a 64-hex SHA-256 (64 bytes) with PKCS7 padding is a multiple of the
    # 16-byte AES block → its hex length is a multiple of 32.
    assert len(out) % 32 == 0


def test_encrypt_password_differs_by_input():
    assert encrypt_password(ENCRYPT_KEY, "aaaa") != encrypt_password(ENCRYPT_KEY, "aaab")
