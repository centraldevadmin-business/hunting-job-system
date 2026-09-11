"""
Module 0 test: config loader reads settings.yaml + targets.yaml.
"""
from src.config_loader import load_config


def test_config_loads():
    cfg = load_config()
    # settings.yaml has target_countries
    assert len(cfg.target_countries()) > 0
    # min_salary default
    assert cfg.min_salary() >= 0
    # match thresholds present
    assert "auto_queue_top" in cfg.match_thresholds()


def test_targets_load():
    cfg = load_config()
    names = cfg.target_names()
    assert "GitLab" in names
    assert "Automattic" in names
    assert "Deel" in names
