"""
Pytest fixtures.

Provides an isolated temp database for tests so the real career.db is never
touched. Each test gets a fresh, empty schema.
"""
import pytest

from src.db.repository import db_path, init_db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the DB at a temp file and initialize the schema."""
    settings = {
        "paths": {"db": str(tmp_path / "test_career.db")}
    }
    monkeypatch.setattr("src.db.repository.load_settings", lambda: settings)
    init_db(settings)
    return settings
