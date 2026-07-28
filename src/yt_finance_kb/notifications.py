from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from hashlib import sha256
from pathlib import Path

import markdown


def _without_front_matter(markdown_body: str) -> str:
    """Remove repository-only YAML metadata from an email body."""
    normalized = markdown_body.lstrip("\ufeff")
    if not normalized.startswith("---\n"):
        return normalized
    _, separator, content = normalized[4:].partition("\n---\n")
    return content.lstrip() if separator else normalized


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


def email_recipients() -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    for value in os.environ.get("EMAIL_TO", "").split(","):
        address = value.strip()
        normalized = address.casefold()
        if address and normalized not in seen:
            recipients.append(address)
            seen.add(normalized)
    return recipients


def configured_email_providers() -> list[str]:
    requested = os.environ.get("EMAIL_PROVIDER", "auto").strip().lower() or "auto"
    if requested not in {"auto", "gmail", "resend"}:
        raise ValueError("EMAIL_PROVIDER must be auto, gmail, or resend")
    available = {
        "gmail": bool(os.environ.get("GMAIL_USERNAME") and os.environ.get("GMAIL_APP_PASSWORD")),
        "resend": bool(os.environ.get("RESEND_API_KEY") and os.environ.get("EMAIL_FROM")),
    }
    if requested == "auto":
        return [provider for provider in ("gmail", "resend") if available[provider]]
    return [requested] if available[requested] else []


def _email_content(markdown_body: str, note_url: str, video_url: str) -> tuple[str, str]:
    visible_markdown = _without_front_matter(markdown_body)
    html = markdown.markdown(visible_markdown, extensions=["extra"])
    html += f'<hr><p><a href="{note_url}">GitHub 原文</a> · <a href="{video_url}">YouTube 视频</a></p>'
    text = f"{visible_markdown.rstrip()}\n\nGitHub 原文：{note_url}\nYouTube 视频：{video_url}\n"
    return text, html


def _send_resend_one(
    recipient: str, title: str, html: str, idempotency_key: str
) -> None:
    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["EMAIL_FROM"]
    recipient_key = sha256(recipient.casefold().encode("utf-8")).hexdigest()[:16]
    _post_json(
        "https://api.resend.com/emails",
        {
            "from": sender,
            "to": [recipient],
            "subject": f"财经知识库｜{title}",
            "html": html,
        },
        {
            "Authorization": f"Bearer {api_key}",
            "Idempotency-Key": f"{idempotency_key}-{recipient_key}"[:256],
        },
    )


def _send_gmail_one(recipient: str, title: str, text: str, html: str) -> None:
    username = os.environ["GMAIL_USERNAME"]
    message = EmailMessage()
    message["From"] = os.environ.get("GMAIL_FROM") or f"财经知识库 <{username}>"
    message["To"] = recipient
    message["Subject"] = f"财经知识库｜{title}"
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=45) as smtp:
        smtp.login(username, os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(message)


def send_email(
    title: str,
    markdown_body: str,
    note_url: str,
    video_url: str,
    idempotency_key: str,
    recipients: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    targets = recipients if recipients is not None else email_recipients()
    providers = configured_email_providers()
    if not targets:
        raise RuntimeError("EMAIL_TO has no valid recipients")
    if not providers:
        raise RuntimeError("No configured email provider is available")
    text, html = _email_content(markdown_body, note_url, video_url)
    results: dict[str, dict[str, str]] = {}
    for recipient in targets:
        errors: list[str] = []
        for provider in providers:
            try:
                if provider == "gmail":
                    _send_gmail_one(recipient, title, text, html)
                else:
                    _send_resend_one(recipient, title, html, idempotency_key)
                results[recipient] = {"status": "sent", "provider": provider}
                break
            except Exception as error:
                errors.append(f"{provider}: {type(error).__name__}: {error}")
        else:
            results[recipient] = {"status": "failed", "error": " | ".join(errors)[:600]}
    return results


def test_email(root: Path, records: dict, repository_url: str, branch: str = "main") -> str:
    completed = [
        record
        for record in records.values()
        if record.get("analysis_status") == "complete" and record.get("note_path")
    ]
    if not completed:
        raise RuntimeError("No completed knowledge note is available for an email test")
    record = max(completed, key=lambda item: (item.get("published_at", ""), item["video_id"]))
    note_path = record["note_path"]
    results = send_email(
        f"[测试] {record['title']}",
        read_note(root, note_path),
        f"{repository_url.rstrip('/')}/blob/{branch}/{note_path}",
        record["video_url"],
        f"email-test-{datetime.now(timezone.utc).isoformat()}",
    )
    failures = [
        f"{recipient}: {result.get('error', 'unknown error')}"
        for recipient, result in results.items()
        if result["status"] != "sent"
    ]
    if failures:
        raise RuntimeError("Email test failed: " + " ; ".join(failures))
    return note_path


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
