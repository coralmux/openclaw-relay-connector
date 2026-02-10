"""Tests for E2E crypto round-trip encryption/decryption."""

import pytest
from openclaw_agent.e2e_crypto import E2ECrypto


def test_keypair_generation():
    """Test that a keypair is generated on init."""
    crypto = E2ECrypto()
    assert crypto.public_key_b64
    assert not crypto.is_ready  # No shared key yet


def test_key_exchange_round_trip():
    """Test that two E2ECrypto instances can derive a shared key."""
    alice = E2ECrypto()
    bob = E2ECrypto()

    # Exchange public keys
    alice.derive_shared_key(bob.public_key_b64)
    bob.derive_shared_key(alice.public_key_b64)

    assert alice.is_ready
    assert bob.is_ready


def test_encrypt_decrypt_round_trip():
    """Test that data encrypted by one party can be decrypted by the other."""
    alice = E2ECrypto()
    bob = E2ECrypto()

    alice.derive_shared_key(bob.public_key_b64)
    bob.derive_shared_key(alice.public_key_b64)

    plaintext = "Hello, World! 안녕하세요!"
    encrypted = alice.encrypt(plaintext)

    assert "ciphertext" in encrypted
    assert "nonce" in encrypted
    assert encrypted["ciphertext"] != plaintext

    decrypted = bob.decrypt(encrypted["ciphertext"], encrypted["nonce"])
    assert decrypted == plaintext


def test_payload_encrypt_decrypt():
    """Test encrypt_payload / decrypt_payload round-trip."""
    alice = E2ECrypto()
    bob = E2ECrypto()

    alice.derive_shared_key(bob.public_key_b64)
    bob.derive_shared_key(alice.public_key_b64)

    payload = {"text": "Test message", "seq": 1, "unicode": "한글"}
    encrypted = alice.encrypt_payload(payload)
    assert encrypted.get("enc") is True

    decrypted = bob.decrypt_payload(encrypted)
    assert decrypted == payload


def test_unencrypted_passthrough():
    """Test that decrypt_payload passes through unencrypted payloads."""
    crypto = E2ECrypto()
    payload = {"text": "plain", "delta": "hello"}
    result = crypto.decrypt_payload(payload)
    assert result == payload


def test_encrypt_before_key_exchange():
    """Test that encrypting before key exchange raises an error."""
    crypto = E2ECrypto()
    with pytest.raises(RuntimeError, match="Key exchange not completed"):
        crypto.encrypt("test")


def test_decrypt_before_key_exchange():
    """Test that decrypting before key exchange raises an error."""
    crypto = E2ECrypto()
    with pytest.raises(RuntimeError, match="Key exchange not completed"):
        crypto.decrypt("base64data", "base64nonce")


def test_invalid_peer_key():
    """Test that invalid peer key raises and resets state."""
    crypto = E2ECrypto()
    with pytest.raises(Exception):
        crypto.derive_shared_key("not-valid-base64-key")
    assert not crypto.is_ready


def test_different_keys_fail_decrypt():
    """Test that data encrypted with one key pair cannot be decrypted by unrelated pair."""
    alice = E2ECrypto()
    bob = E2ECrypto()
    carol = E2ECrypto()

    alice.derive_shared_key(bob.public_key_b64)
    bob.derive_shared_key(alice.public_key_b64)

    # Carol independently derives with herself (wrong key)
    carol.derive_shared_key(E2ECrypto().public_key_b64)

    encrypted = alice.encrypt("secret")

    # Bob can decrypt
    assert bob.decrypt(encrypted["ciphertext"], encrypted["nonce"]) == "secret"

    # Carol cannot decrypt (different shared key)
    with pytest.raises(Exception):
        carol.decrypt(encrypted["ciphertext"], encrypted["nonce"])
