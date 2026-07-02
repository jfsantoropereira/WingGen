"""Configuration loading helpers."""

from __future__ import annotations

import tomllib
from pathlib import Path

from wingopt.config.models import WingGenConfig, build_config


def load_config(path: str | Path) -> WingGenConfig:
    """Load and validate a simulator config file.

    Args:
        path: Path to a TOML configuration file.

    Returns:
        Parsed and validated simulator configuration.
    """

    config_path = Path(path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return build_config(raw)
