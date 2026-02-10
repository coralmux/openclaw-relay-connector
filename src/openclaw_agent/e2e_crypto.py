"""End-to-end encryption: X25519 key exchange + AES-256-GCM."""

import base64
import json
import logging
import os
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)


class E2ECrypto:
    """Handles X25519 key exchange and AES-256-GCM encryption/decryption."""

    def __init__(self):
        self._private_key = X25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        self._shared_key: Optional[bytes] = None

    @property
    def public_key_b64(self) -> str:
        """Our public key as base64 for sending to peer."""
        raw = self._public_key.public_bytes_raw()
        return base64.b64encode(raw).decode()

    def derive_shared_key(self, peer_pubkey_b64: str) -> None:
        """Derive shared secret from peer's X25519 public key."""
        try:
            peer_raw = base64.b64decode(peer_pubkey_b64)
            peer_key = X25519PublicKey.from_public_bytes(peer_raw)
            shared_secret = self._private_key.exchange(peer_key)

            # HKDF to derive a proper AES-256 key
            self._shared_key = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"openclaw-relay-e2e-v1",
                info=b"chat-encryption",
            ).derive(shared_secret)
        except Exception as e:
            logger.error(f"Failed to derive shared key: {e}")
            self._shared_key = None
            raise

    @property
    def is_ready(self) -> bool:
        return self._shared_key is not None

    def encrypt(self, plaintext: str) -> dict:
        """Encrypt a string, returns {"ciphertext": ..., "nonce": ...} both base64."""
        if not self._shared_key:
            raise RuntimeError("Key exchange not completed")

        nonce = os.urandom(12)  # 96-bit nonce for AES-GCM
        aesgcm = AESGCM(self._shared_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

        return {
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "nonce": base64.b64encode(nonce).decode(),
        }

    def decrypt(self, ciphertext_b64: str, nonce_b64: str) -> str:
        """Decrypt a ciphertext+nonce pair back to string."""
        if not self._shared_key:
            raise RuntimeError("Key exchange not completed")

        ciphertext = base64.b64decode(ciphertext_b64)
        nonce = base64.b64decode(nonce_b64)
        aesgcm = AESGCM(self._shared_key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        return plaintext.decode("utf-8")

    def encrypt_payload(self, payload: dict) -> dict:
        """Encrypt an entire payload dict, returns encrypted wrapper."""
        plaintext = json.dumps(payload, ensure_ascii=False)
        encrypted = self.encrypt(plaintext)
        encrypted["enc"] = True
        return encrypted

    def decrypt_payload(self, payload: dict) -> dict:
        """Decrypt an encrypted payload wrapper back to original dict."""
        if not payload.get("enc"):
            return payload  # Not encrypted, pass through
        plaintext = self.decrypt(payload["ciphertext"], payload["nonce"])
        return json.loads(plaintext)
