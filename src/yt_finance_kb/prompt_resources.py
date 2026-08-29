from __future__ import annotations

from importlib.resources import files
from pathlib import Path


QUALITY_PROMPT_RESOURCE = "prompt_assets/finance_quality.txt"


def load_quality_prompt(path: Path | None = None) -> str:
    """Load the production quality instructions or an explicit candidate file."""
    if path is not None:
        value = path.read_text(encoding="utf-8")
    else:
        value = files("yt_finance_kb").joinpath(QUALITY_PROMPT_RESOURCE).read_text(encoding="utf-8")
    value = value.strip()
    if not value:
        raise ValueError("Finance quality prompt cannot be empty")
    return value
