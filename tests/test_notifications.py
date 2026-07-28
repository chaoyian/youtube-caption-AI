import json

import yt_finance_kb.pipeline as pipeline
import yt_finance_kb.notifications as notifications
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
    for name in (
        "RESEND_API_KEY",
        "EMAIL_FROM",
        "EMAIL_TO",
        "EMAIL_PROVIDER",
        "GMAIL_USERNAME",
        "GMAIL_APP_PASSWORD",
        "DISCORD_WEBHOOK_URL",
    ):
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
    monkeypatch.setattr(
        pipeline,
        "send_email",
        lambda *args: sent.append("email")
        or {"to@example.com": {"status": "sent", "provider": "resend"}},
    )
    monkeypatch.setattr(pipeline, "send_discord", lambda *args: sent.append("discord"))
    pipeline.notify(tmp_path, state_path, "https://github.com/example/repo")
    assert sent == ["email", "discord"]


def test_auto_email_uses_gmail_first_and_sends_recipients_individually(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "auto")
    monkeypatch.setenv("EMAIL_TO", "one@example.com,two@example.com,ONE@example.com")
    monkeypatch.setenv("GMAIL_USERNAME", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("RESEND_API_KEY", "resend-key")
    monkeypatch.setenv("EMAIL_FROM", "KB <notes@example.com>")
    gmail_calls = []
    resend_calls = []
    monkeypatch.setattr(
        notifications,
        "_send_gmail_one",
        lambda recipient, *args: gmail_calls.append(recipient),
    )
    monkeypatch.setattr(
        notifications,
        "_send_resend_one",
        lambda recipient, *args: resend_calls.append(recipient),
    )
    results = notifications.send_email(
        "节目", "# 笔记", "https://github.invalid/note", "https://youtu.be/video", "key"
    )
    assert gmail_calls == ["one@example.com", "two@example.com"]
    assert resend_calls == []
    assert {result["provider"] for result in results.values()} == {"gmail"}


def test_auto_email_falls_back_to_resend_per_recipient(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "auto")
    monkeypatch.setenv("EMAIL_TO", "one@example.com,two@example.com")
    monkeypatch.setenv("GMAIL_USERNAME", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("RESEND_API_KEY", "resend-key")
    monkeypatch.setenv("EMAIL_FROM", "KB <notes@example.com>")
    monkeypatch.setattr(
        notifications,
        "_send_gmail_one",
        lambda recipient, *args: (
            (_ for _ in ()).throw(RuntimeError("gmail unavailable"))
            if recipient == "two@example.com"
            else None
        ),
    )
    resend_calls = []
    monkeypatch.setattr(
        notifications,
        "_send_resend_one",
        lambda recipient, *args: resend_calls.append(recipient),
    )
    results = notifications.send_email(
        "节目", "# 笔记", "https://github.invalid/note", "https://youtu.be/video", "key"
    )
    assert results["one@example.com"]["provider"] == "gmail"
    assert results["two@example.com"]["provider"] == "resend"
    assert resend_calls == ["two@example.com"]


def test_notification_retry_only_resends_failed_recipient(tmp_path, monkeypatch):
    note = tmp_path / "knowledge/channel/2026/note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# 笔记", encoding="utf-8")
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
                        "video_url": "https://youtu.be/abcdefghijk",
                        "analysis_status": "complete",
                        "note_path": "knowledge/channel/2026/note.md",
                        "note_version": 1,
                        "email_status": "pending",
                        "discord_status": "disabled",
                        "alert_status": "none",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    monkeypatch.setenv("EMAIL_TO", "one@example.com,two@example.com")
    monkeypatch.setenv("GMAIL_USERNAME", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    calls = []

    def fake_send(*args):
        recipients = args[-1]
        calls.append(recipients)
        return {
            recipient: {
                "status": "sent" if recipient == "one@example.com" or len(calls) > 1 else "failed",
                "provider": "gmail",
            }
            for recipient in recipients
        }

    monkeypatch.setattr(pipeline, "send_email", fake_send)
    first = pipeline.notify(tmp_path, state_path, "https://github.com/example/repo")
    second = pipeline.notify(tmp_path, state_path, "https://github.com/example/repo")
    record = json.loads(state_path.read_text(encoding="utf-8"))["videos"]["abcdefghijk"]
    assert first == {"email": 1, "discord": 0, "alerts": 0, "failed": 1}
    assert second == {"email": 1, "discord": 0, "alerts": 0, "failed": 0}
    assert calls == [
        ["one@example.com", "two@example.com"],
        ["two@example.com"],
    ]
    assert record["email_status"] == "sent"
