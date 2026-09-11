"""
Module 15 — Local Security Layer (encryption + backups + audit) tests.

Verifies:
  - Fernet encryption of the DB file at rest (random-key and passphrase modes)
  - Timestamped backups + retention pruning + restore
  - Audit trail recording and summarization

All tests run against an isolated temp SQLite DB. No LLM, no network.
Uses the `tmp_db` fixture from conftest.py.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _tmp_settings(tmp_path):
    """Build settings pointing at a temp DB *and* temp backups dir."""
    return {
        "paths": {
            "db": str(tmp_path / "career.db"),
            "backups": str(tmp_path / "backups"),
        }
    }


# --------------------------------------------------------------------------- #
# Encryption
# --------------------------------------------------------------------------- #
def test_encrypt_db_creates_encrypted_sidecar(tmp_db):
    """encrypt_db produces a .enc file next to the DB."""
    from src import security

    enc = security.encrypt.encrypt_db(tmp_db)
    assert enc.exists()
    assert security.encrypt.is_encrypted(tmp_db)
    # The plaintext DB is untouched (we encrypt, not move).
    assert os.path.exists(str(enc).replace(".enc", ""))


def test_encrypt_decrypt_roundtrip(tmp_db):
    """decrypt_db reproduces the original DB bytes."""
    from src import security

    src = security.encrypt.db_path(tmp_db)
    before = src.read_bytes()

    security.encrypt.encrypt_db(tmp_db)
    dec = security.encrypt.decrypt_db(tmp_db)
    after = dec.read_bytes()

    assert before == after


def test_encrypt_status_reports_state(tmp_db):
    """status() returns a dict describing the encryption state."""
    from src import security

    st = security.encrypt.status(tmp_db)
    assert st["db_bytes"] > 0
    assert st["encrypted_exists"] is False

    security.encrypt.encrypt_db(tmp_db)
    st = security.encrypt.status(tmp_db)
    assert st["encrypted_exists"] is True
    assert st["encrypted_bytes"] > 0
    assert st["key_exists"] is True


def test_encrypt_passphrase_mode(tmp_db):
    """Passphrase mode derives a key from settings and persists a salt."""
    from src import security

    settings = {
        **tmp_db,
        "security": {"encryption": {"enabled": True, "passphrase": "hunter-secret-123"}},
    }
    security.encrypt.encrypt_db(settings)

    kf = security.encrypt.key_file_path(settings)
    assert kf.exists()
    # Salt file is stored alongside the key.
    assert os.path.exists(str(kf) + ".salt")

    # A fresh get_key re-derives the same key from the passphrase.
    key = security.encrypt.get_key(settings)
    assert key is not None

    # Round-trip works.
    dec = security.encrypt.decrypt_db(settings)
    assert dec.exists()


def test_encrypt_random_key_mode(tmp_db):
    """Without a passphrase, a random key is generated and persisted."""
    from src import security

    security.encrypt.encrypt_db(tmp_db)
    key = security.encrypt.get_key(tmp_db)
    assert key is not None
    # Key file exists.
    assert security.encrypt.key_file_path(tmp_db).exists()


# --------------------------------------------------------------------------- #
# Backups
# --------------------------------------------------------------------------- #
def test_make_backup_creates_timestamped_file(tmp_path):
    """make_backup copies the DB to a timestamped file in the backups dir."""
    from src import security

    settings = _tmp_settings(tmp_path)
    from src.db.repository import init_db

    init_db(settings)

    backups = security.backup.list_backups(settings)
    assert backups == []

    b = security.backup.make_backup(settings)
    assert b.exists()
    assert b.name.startswith("career.db.")
    assert b.name.endswith(".sqlite")

    backups = security.backup.list_backups(settings)
    assert len(backups) == 1
    assert backups[0]["bytes"] > 0


def test_prune_backups_keeps_recent(tmp_path):
    """prune_backups keeps only the most recent N backups."""
    from src import security

    settings = _tmp_settings(tmp_path)
    from src.db.repository import init_db

    init_db(settings)

    # Create 5 backups.
    for _ in range(5):
        security.backup.make_backup(settings)

    deleted = security.backup.prune_backups(settings, keep=2)
    assert deleted == 3
    assert len(security.backup.list_backups(settings)) == 2


def test_restore_backup(tmp_path):
    """restore_backup overwrites the live DB from the most recent backup."""
    from src import security

    settings = _tmp_settings(tmp_path)
    from src.db.repository import init_db

    init_db(settings)

    security.backup.make_backup(settings)
    restored = security.backup.restore_backup(settings)
    assert restored.exists()
    assert restored.stat().st_size > 0


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def test_audit_records_and_summarizes(tmp_db):
    """record() writes an audit event; summarize() counts by type."""
    from src import security

    security.audit.record("backup", "created career.db.20260101.sqlite", tmp_db)
    security.audit.record("decrypt_attempt", "ok", tmp_db)
    security.audit.record("backup", "created career.db.20260102.sqlite", tmp_db)

    assert security.audit.count(tmp_db) == 3
    summary = security.audit.summarize(tmp_db)
    assert summary["backup"] == 2
    assert summary["decrypt_attempt"] == 1

    recent = security.audit.recent(tmp_db, limit=10)
    assert len(recent) == 3
    # Newest first.
    assert recent[0]["event"] == "backup"


def test_audit_since_filters_by_time(tmp_db):
    """since() returns events within the given window."""
    from src import security

    security.audit.record("backup", "recent", tmp_db)
    # A freshly recorded event is within the last hour.
    assert len(security.audit.since(tmp_db, hours=1)) == 1
    # A window in the past excludes it.
    assert security.audit.since(tmp_db, hours=-1) == []
