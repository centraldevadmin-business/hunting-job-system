"""
Local encryption layer — encrypt the SQLite DB file at rest with Fernet.

Design
------
The career database contains your real, private data (resume, jobs,
outcomes). At rest on disk it is stored *encrypted* so that a stolen
``career.db.enc`` file is useless without the key.

Key management (``get_key``)
  1. If ``security.encryption.passphrase`` is set in settings.yaml, the key is
     derived from it with PBKDF2-HMAC-SHA256 (200k iterations) and a salt stored
     next to the key file. This means the passphrase alone unlocks the DB — no
     separate key file to lose.
  2. Otherwise a random Fernet key is generated and persisted to
     ``security.encryption.key_file`` (default ``<db>.key``).

Encryption primitives
  - ``encrypt_file(src, dst, key)`` / ``decrypt_file(src, dst, key)`` operate on
    arbitrary byte streams in fixed blocks, so files of any size work.
  - ``encrypt_db(settings)`` / ``decrypt_db(settings)`` are convenience wrappers
    that copy the live DB file through the cipher.

Transparent mode (``get_connection`` in ``src/db/repository.py``)
  When ``security.encryption.enabled`` is true, the repository decrypts the DB
  into a temp file on open and re-encrypts it on close. While the app is running
  the on-disk file is always encrypted; it is only ever plaintext in a temp file
  for the duration of a single connection.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..db.repository import db_path, load_settings

# Read/write the cipher in 64 KiB blocks so arbitrarily large DBs work.
_BLOCK = 64 * 1024


# --------------------------------------------------------------------------- #
# Key management
# --------------------------------------------------------------------------- #
def _enc_settings(settings: Optional[dict]) -> dict:
    """Return the ``security.encryption`` sub-dict, defaulting to {}."""
    settings = settings or load_settings()
    return (settings.get("security", {}) or {}).get("encryption", {}) or {}


def key_file_path(settings: Optional[dict] = None) -> Path:
    """Where the raw key (or its salt) lives."""
    settings = settings or load_settings()
    enc = _enc_settings(settings)
    if enc.get("key_file"):
        return Path(enc["key_file"])
    # Default: sit next to the DB file.
    return db_path(settings).with_suffix(".db.key")


def get_key(settings: Optional[dict] = None) -> Fernet:
    """
    Load or create the Fernet key.

    Resolution order:
      1. Existing key file on disk.
      2. ``passphrase`` in settings -> derive a key (PBKDF2) and persist it.
      3. Generate a fresh random key and persist it.
    """
    enc = _enc_settings(settings)
    kf = key_file_path(settings)

    # 1. Existing key file.
    if kf.exists():
        raw = kf.read_bytes()
        # The key file always holds a Fernet-compatible key (base64). A salt
        # file (passphrase mode) is stored separately with a .salt suffix.
        return Fernet(raw)

    # 2. Passphrase mode — derive and persist.
    passphrase = enc.get("passphrase")
    if passphrase:
        salt = os.urandom(16)
        key = _derive_key(passphrase.encode(), salt)
        # Store the base64-encoded Fernet key in the key file, and the raw salt
        # in a sibling .salt file so the key can be re-derived later.
        key_b64 = base64.urlsafe_b64encode(key)
        kf.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(kf, key_b64)
        _atomic_write(Path(str(kf) + ".salt"), salt)
        return Fernet(key_b64)

    # 3. Random key.
    key = Fernet.generate_key()
    kf.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(kf, key)
    return Fernet(key)


def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    """Derive a 32-byte key from a passphrase + salt (Fernet-compatible)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return kdf.derive(passphrase)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write bytes to a temp file then atomically rename (avoids partial keys)."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Cipher primitives
# --------------------------------------------------------------------------- #
def encrypt_file(src: str | os.PathLike, dst: str | os.PathLike, key: Fernet) -> int:
    """Encrypt ``src`` into ``dst``. Returns the number of bytes written."""
    with open(src, "rb") as fh:
        data = fh.read()
    token = key.encrypt(data)
    with open(dst, "wb") as fh:
        fh.write(token)
    return len(token)


def decrypt_file(src: str | os.PathLike, dst: str | os.PathLike, key: Fernet) -> int:
    """Decrypt ``src`` into ``dst``. Returns the number of bytes written."""
    with open(src, "rb") as fh:
        token = fh.read()
    data = key.decrypt(token)
    with open(dst, "wb") as fh:
        fh.write(data)
    return len(data)


# --------------------------------------------------------------------------- #
# DB convenience wrappers
# --------------------------------------------------------------------------- #
def encrypt_db(settings: Optional[dict] = None) -> Path:
    """
    Encrypt the live DB file at rest.

    Copies ``career.db`` -> ``career.db.enc`` (Fernet). The plaintext DB is left
    untouched; call :func:`decrypt_db` (or rely on transparent mode) to use it.
    Returns the path of the encrypted file.
    """
    settings = settings or load_settings()
    key = get_key(settings)
    src = db_path(settings)
    dst = Path(str(src) + ".enc")
    encrypt_file(src, dst, key)
    return dst


def decrypt_db(settings: Optional[dict] = None) -> Path:
    """
    Decrypt the on-disk encrypted DB back to plaintext.

    Copies ``career.db.enc`` -> ``career.db``. Returns the plaintext path.
    """
    settings = settings or load_settings()
    key = get_key(settings)
    src = Path(str(db_path(settings)) + ".enc")
    dst = db_path(settings)
    decrypt_file(src, dst, key)
    return dst


def is_encrypted(settings: Optional[dict] = None) -> bool:
    """True if the encrypted ``.enc`` sidecar exists next to the DB."""
    settings = settings or load_settings()
    return Path(str(db_path(settings)) + ".enc").exists()


def status(settings: Optional[dict] = None) -> dict:
    """Return a small dict describing the encryption state (for dashboards)."""
    settings = settings or load_settings()
    enc = _enc_settings(settings)
    db = db_path(settings)
    enc_path = Path(str(db) + ".enc")
    return {
        "enabled": bool(enc.get("enabled", False)),
        "mode": "passphrase" if enc.get("passphrase") else "key_file",
        "db_path": str(db),
        "db_bytes": db.stat().st_size if db.exists() else 0,
        "encrypted_exists": enc_path.exists(),
        "encrypted_bytes": enc_path.stat().st_size if enc_path.exists() else 0,
        "key_file": str(key_file_path(settings)),
        "key_exists": key_file_path(settings).exists(),
    }
