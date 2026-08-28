from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from .models import AppConfig


CHANNELS_ENV_VAR = "YOUTUBE_CHANNELS_JSON"


def _config_from_environment(value: str) -> AppConfig:
    try:
        data: Any = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{CHANNELS_ENV_VAR} must contain valid JSON: {error.msg}") from error
    if isinstance(data, list):
        data = {"channels": data}
    if not isinstance(data, dict):
        raise ValueError(
            f'{CHANNELS_ENV_VAR} must be a channel array or an object with a "channels" array'
        )
    return AppConfig.model_validate(data)


def load_config(path: Path) -> AppConfig:
    environment_config = os.environ.get(CHANNELS_ENV_VAR, "").strip()
    if environment_config:
        return _config_from_environment(environment_config)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return AppConfig.model_validate(data)
