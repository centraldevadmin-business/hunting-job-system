"""
Config loader — reads settings.yaml and targets.yaml.

Provides typed accessors so modules never parse YAML themselves.
All values are optional; missing keys fall back to sensible defaults.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.db.repository import _project_root


def _load_yaml(name: str) -> dict:
    path = _project_root() / "config" / name
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class Config:
    """Immutable-ish config wrapper over settings.yaml + targets.yaml."""

    def __init__(self, settings: dict | None = None, targets: dict | None = None):
        self._settings = settings or _load_yaml("settings.yaml")
        self._targets = targets or _load_yaml("targets.yaml")

    # ----- settings accessors -----
    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def target_countries(self) -> list[str]:
        return self._settings.get("target_countries", [])

    def min_salary(self) -> int:
        return int(self._settings.get("min_salary_usd", 30000))

    def match_thresholds(self) -> dict:
        return self._settings.get("match_thresholds", {})

    def outcomes_before_trusted(self) -> int:
        return int(self._settings.get("outcomes_before_trusted", 30))

    def dry_market_days(self) -> int:
        return int(self._settings.get("dry_market_days", 7))

    def maintenance_mode_days(self) -> int:
        return int(self._settings.get("maintenance_mode_days", 14))

    def followup_after_days(self) -> int:
        return int(self._settings.get("followup_after_days", 14))

    def resume_variants_per_job(self) -> int:
        return int(self._settings.get("resume_variants_per_job", 3))

    def paths(self) -> dict:
        return self._settings.get("paths", {})

    # ----- targets accessors -----
    def targets(self) -> list[dict]:
        return self._targets.get("targets", [])

    def target_names(self) -> list[str]:
        return [t["name"] for t in self.targets()]


def load_config() -> Config:
    """Load the global config instance."""
    return Config()
