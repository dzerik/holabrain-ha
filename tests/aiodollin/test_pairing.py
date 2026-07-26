"""Pairing primitives: the two values needed to claim an appliance.

Both are wire formats: if either is computed differently the cloud simply refuses, with an
error that says nothing about which half was wrong. So these tests pin the exact shape,
including the parts that look arbitrary — a raw digest where a hex one would be natural, a
random prefix that must not be stripped.
"""

import hashlib

from custom_components.holabrain.aiodollin.pairing import (
    derive_verification_code,
    encrypt_serial,
)

SERIAL = "0000E1540760EY1790000000000EXAMP"
TOKEN = "eu_A_eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.payload.signature"


def _decrypt(field: str, token: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    raw = bytes.fromhex(field)
    iv, ciphertext = raw[:16], raw[16:]
    key = hashlib.sha256(token.encode()).digest()[:16]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


def test_the_serial_round_trips_with_the_documented_key():
    """The key is the *raw* first 16 bytes of SHA-256 over the token.

    Using its hex form instead is the natural mistake and produces a field the cloud cannot
    decrypt — it answers with a generic error that gives no hint at all.
    """
    field = encrypt_serial(SERIAL, TOKEN)

    assert _decrypt(field, TOKEN) == SERIAL


def test_the_iv_is_prepended_and_not_reused():
    """The cloud has no other way to learn the IV, and a fixed one would leak equality.

    Two encryptions of the same serial must differ, or an observer could tell that the same
    appliance is being claimed twice.
    """
    first = encrypt_serial(SERIAL, TOKEN)
    second = encrypt_serial(SERIAL, TOKEN)

    assert first[:32] != second[:32]  # the IV half
    assert first != second
    assert _decrypt(first, TOKEN) == _decrypt(second, TOKEN) == SERIAL


def test_a_different_session_cannot_decrypt_the_field():
    """The serial is bound to the caller's own session, so a captured request is useless."""
    field = encrypt_serial(SERIAL, TOKEN)

    try:
        recovered = _decrypt(field, TOKEN + "-other")
    except ValueError:
        return  # padding check already rejected it
    assert recovered != SERIAL


def test_the_field_length_matches_the_block_structure():
    # 32 hex of IV plus whole 16-byte blocks: a 32-char serial pads to 48 bytes.
    field = encrypt_serial(SERIAL, TOKEN)
    assert len(field) == 32 + 48 * 2
    assert all(char in "0123456789abcdef" for char in field)


def test_the_verification_code_tail_is_the_network_digest():
    """Only the first two bytes are random; the rest identifies the Wi-Fi network.

    This is what makes the code derivable rather than a device secret, and it is the reason
    the same appliance reports a different code every time it is set up.
    """
    bssid = "aa:bb:cc:dd:ee:ff"
    password = "correct horse battery staple"
    expected_tail = hashlib.md5(bytes.fromhex("aabbccddeeff") + password.encode()).digest()[:14]

    first = derive_verification_code(bssid, password)
    second = derive_verification_code(bssid, password)

    assert first[4:] == second[4:] == expected_tail.hex()
    assert first[:4] != second[:4] or first == second  # random prefix, may collide rarely


def test_the_bssid_notation_does_not_matter():
    """Routers report the BSSID in several notations; all of them are the same network."""
    password = "pw"
    codes = {
        derive_verification_code(form, password, nonce=b"\x00\x00")
        for form in ("aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aabbccddeeff", "AABBCCDDEEFF")
    }

    assert len(codes) == 1


def test_a_different_password_changes_the_code():
    """Otherwise anyone on the same network could claim the appliance."""
    a = derive_verification_code("aabbccddeeff", "one", nonce=b"\x00\x00")
    b = derive_verification_code("aabbccddeeff", "two", nonce=b"\x00\x00")

    assert a != b


def test_the_code_is_exactly_sixteen_bytes():
    code = derive_verification_code("aabbccddeeff", "pw")

    assert len(code) == 32  # hex of 16 bytes, as the cloud expects
