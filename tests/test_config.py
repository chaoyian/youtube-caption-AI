from __future__ import annotations

import json

import pytest

from yt_finance_kb.config import load_config


def test_environment_channel_array_overrides_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "channels.yaml"
    config_path.write_text(
        "channels:\n  - id: yaml-channel\n    url: https://www.youtube.com/@yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "YOUTUBE_CHANNELS_JSON",
        json.dumps(
            [
                {
                    "id": "github-channel",
                    "url": "https://www.youtube.com/@github",
                    "youtube_channel_id": "UCabcdefghijklmnopqrstuv",
                }
            ]
        ),
    )

    config = load_config(config_path)

    assert [channel.id for channel in config.channels] == ["github-channel"]


def test_environment_channels_object_is_supported(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "YOUTUBE_CHANNELS_JSON",
        '{"channels":[{"id":"object-channel","url":"https://www.youtube.com/@object"}]}',
    )

    config = load_config(tmp_path / "missing.yaml")

    assert [channel.id for channel in config.channels] == ["object-channel"]


def test_invalid_environment_json_has_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_CHANNELS_JSON", "not-json")

    with pytest.raises(ValueError, match="YOUTUBE_CHANNELS_JSON must contain valid JSON"):
        load_config(tmp_path / "missing.yaml")


def test_blank_environment_value_uses_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "channels.yaml"
    config_path.write_text(
        "channels:\n  - id: yaml-channel\n    url: https://www.youtube.com/@yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YOUTUBE_CHANNELS_JSON", "   ")

    config = load_config(config_path)

    assert [channel.id for channel in config.channels] == ["yaml-channel"]
