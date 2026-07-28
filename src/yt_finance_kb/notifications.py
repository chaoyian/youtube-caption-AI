from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import markdown


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json", "User-Agent": "youtube-finance-kb/0.1"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Notification request failed with HTTP {error.code}: {detail}") from error
    return json.loads(data) if data else {}


def send_email(title: str, markdown_body: str, note_url: str, video_url: str, idempotency_key: str) -> None:
    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["EMAIL_FROM"]
    recipient = os.environ["EMAIL_TO"]
    html = markdown.markdown(markdown_body, extensions=["extra"])
    html += f'<hr><p><a href="{note_url}">GitHub 原文</a> · <a href="{video_url}">YouTube 视频</a></p>'
    _post_json(
        "https://api.resend.com/emails",
        {
            "from": sender,
            "to": [address.strip() for address in recipient.split(",") if address.strip()],
            "subject": f"财经知识库｜{title}",
            "html": html,
        },
        {
            "Authorization": f"Bearer {api_key}",
            "Idempotency-Key": idempotency_key[:256],
        },
    )


def _discord_summary(markdown_body: str, note_url: str, video_url: str) -> str:
    marker = "## 金融摘要\n\n"
    summary = markdown_body.split(marker, 1)[1].split("\n## ", 1)[0].strip() if marker in markdown_body else ""
    content = f"{summary}\n\n[完整笔记]({note_url}) · [观看视频]({video_url})"
    return content[:2000]


def send_discord(title: str, markdown_body: str, note_url: str, video_url: str) -> None:
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    _post_json(
        webhook + ("&" if "?" in webhook else "?") + "wait=true",
        {
            "username": "财经知识库",
            "embeds": [
                {
                    "title": title[:256],
                    "description": _discord_summary(markdown_body, note_url, video_url),
                    "url": video_url,
                    "color": 0x1F8B4C,
                }
            ],
            "allowed_mentions": {"parse": []},
        },
    )


def send_discord_alert(message: str) -> None:
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    _post_json(
        webhook + ("&" if "?" in webhook else "?") + "wait=true",
        {"username": "财经知识库告警", "content": ("⚠️ " + message)[:2000], "allowed_mentions": {"parse": []}},
    )


def read_note(root: Path, note_path: str) -> str:
    return (root / note_path).read_text(encoding="utf-8")

