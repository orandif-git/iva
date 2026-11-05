import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no look-alikes (0/o, 1/l/i): customers type this on a recorder


class KeyVault:
    """Encrypts recorder keys at rest. ISUP-style protocols need the real key to verify a device,
    so it cannot be a one-way hash. The device id is bound in as associated data, so a ciphertext
    copied onto another recorder's row will not decrypt. Swap this class for a KMS call in production."""

    def __init__(self, kek: bytes):
        self._aes = AESGCM(kek)

    def encrypt(self, plaintext: str, device_id: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, plaintext.encode(), device_id.encode())

    def decrypt(self, blob: bytes, device_id: str) -> str:
        return self._aes.decrypt(blob[:12], blob[12:], device_id.encode()).decode()


def vault() -> KeyVault:
    return KeyVault(base64.b64decode(get_settings().key_encryption_key))


def generate_key() -> str:
    """12 characters in three groups, about 60 bits. Long enough to resist guessing, short enough to type."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))
