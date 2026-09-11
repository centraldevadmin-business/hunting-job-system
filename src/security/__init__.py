"""Hardening — Local Security Layer (encryption + backups + audit).

Public API:
  - encrypt.encrypt_db / decrypt_db / get_key / status / is_encrypted
  - backup.make_backup / list_backups / prune_backups / restore_backup
  - audit.record / recent / since / summarize / count
"""
from . import audit, backup, encrypt

__all__ = ["encrypt", "backup", "audit"]
