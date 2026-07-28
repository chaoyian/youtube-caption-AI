import json

import yt_finance_kb.pipeline as pipeline
from yt_finance_kb.notifications import _without_front_matter


def test_email_body_excludes_yaml_front_matter():
    body = """---
title: "很长的标题"
video_id: abcdefghijk
topics:
  - 财经
---

# 正常标题

## 金融摘要

摘要正文
"""
    visible = _without_front_matter(body)
    assert visible.startswith("# 正常标题")
    assert "title:" not in visible
    assert "video_id:" not in visible
    assert "## 金融摘要" in visible


def test_notification_retry_does_not_analyze_and_recovers_after_config_added(tmp_path, monkeypatch):
    note = tmp_path / "knowledge/channel/2026/note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# 笔记\n\n## 金融摘要\n\n摘要", encoding="utf-8")
    state_path = tmp_path / "state/videos.json"
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "videos": {
                    "abcdefghijk": {
                        "video_id": "abcdefghijk",
                        "title": "节目",
                        "video_url": "https://www.youtube.com/watch?v=abcdefghijk",
                        "analysis_status": "complete",
                        "note_path": "knowledge/channel/2026/note.md",
                        "note_version": 1,
                        "email_status": "pending",
                        "discord_status": "pending",
                        "alert_status": "none",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    for name in ("RESEND_API_KEY", "EMAIL_FROM", "EMAIL_TO", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)
    pipeline.notify(tmp_path, state_path, "https://github.com/example/repo")
    record = json.loads(state_path.read_text(encoding="utf-8"))["videos"]["abcdefghijk"]
    assert record["email_status"] == "disabled"
    assert record["discord_status"] == "disabled"

    sent = []
    monkeypatch.setenv("RESEND_API_KEY", "test")
    monkeypatch.setenv("EMAIL_FROM", "from@example.com")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid")
    monkeypatch.setattr(pipeline, "send_email", lambda *args: sent.append("email"))
    monkeypatch.setattr(pipeline, "send_discord", lambda *args: sent.append("discord"))
    pipeline.notify(tmp_path, state_path, "https://github.com/example/repo")
    assert sent == ["email", "discord"]
