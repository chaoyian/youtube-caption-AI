from __future__ import annotations

from pathlib import Path

import yaml

from .models import AppConfig


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return AppConfig.model_validate(data)

