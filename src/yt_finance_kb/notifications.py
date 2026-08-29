from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from hashlib import sha256
from html import escape
from pathlib import Path

import markdown


EMAIL_PATTERN = re.compile(r"^[^\s@,<>]+@[^\s@,<>]+\.[^\s@,<>]+$")


def _without_front_matter(markdown_body: str) -> str:
    """Remove repository-only YAML metadata from an email body."""
    normalized = markdown_body.lstrip("\ufeff")
    if not normalized.startswith("---\n"):
        return normalized
    _, separator, content = normalized[4:].partition("\n---\n")
    return content.lstrip() if separator else normalized


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json", "User-Agent": "youtube-finance-kb/0.2"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Notification request failed with HTTP {error.code}: {detail}") from error
    return json.loads(data) if data else {}


def normalize_email_recipients(values: list[str]) -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    for value in values:
        if "\r" in value or "\n" in value:
            raise ValueError("Email addresses cannot contain newlines")
        for item in value.split(","):
            address = item.strip()
            if not address:
                continue
            if not EMAIL_PATTERN.fullmatch(address):
                raise ValueError("Invalid email recipient address")
            normalized = address.casefold()
            if normalized not in seen:
                recipients.append(address)
                seen.add(normalized)
    return recipients


def email_recipients() -> list[str]:
    return normalize_email_recipients([os.environ.get("EMAIL_TO", "")])


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


def _send_resend_one(recipient: str, subject: str, html: str, idempotency_key: str) -> None:
    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["EMAIL_FROM"]
    recipient_key = sha256(recipient.casefold().encode("utf-8")).hexdigest()[:16]
    _post_json(
        "https://api.resend.com/emails",
        {
            "from": sender,
            "to": [recipient],
            "subject": subject,
            "html": html,
        },
        {
            "Authorization": f"Bearer {api_key}",
            "Idempotency-Key": f"{idempotency_key}-{recipient_key}"[:256],
        },
    )


def _send_gmail_one(recipient: str, subject: str, text: str, html: str) -> None:
    username = os.environ["GMAIL_USERNAME"]
    message = EmailMessage()
    message["From"] = os.environ.get("GMAIL_FROM") or f"财经知识库 <{username}>"
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=45) as smtp:
        smtp.login(username, os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(message)


def send_message_email(
    subject: str,
    text: str,
    html: str,
    idempotency_key: str,
    recipients: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    targets = normalize_email_recipients(recipients) if recipients is not None else email_recipients()
    providers = configured_email_providers()
    if not targets:
        raise RuntimeError("No valid email recipients were provided")
    if not providers:
        raise RuntimeError("No configured email provider is available")
    results: dict[str, dict[str, str]] = {}
    for recipient in targets:
        errors: list[str] = []
        for provider in providers:
            try:
                if provider == "gmail":
                    _send_gmail_one(recipient, subject, text, html)
                else:
                    _send_resend_one(recipient, subject, html, idempotency_key)
                results[recipient] = {"status": "sent", "provider": provider}
                break
            except Exception as error:
                errors.append(f"{provider}: {type(error).__name__}: {error}")
        else:
            results[recipient] = {"status": "failed", "error": " | ".join(errors)[:600]}
    return results


def send_email(
    title: str,
    markdown_body: str,
    note_url: str,
    video_url: str,
    idempotency_key: str,
    recipients: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    text, html = _email_content(markdown_body, note_url, video_url)
    return send_message_email(
        f"财经知识库｜{title}", text, html, idempotency_key, recipients
    )


def test_email(
    root: Path,
    records: dict,
    repository_url: str,
    branch: str = "main",
    recipients: list[str] | None = None,
) -> str:
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
        recipients,
    )
    failures = [
        f"{recipient}: {result.get('error', 'unknown error')}"
        for recipient, result in results.items()
        if result["status"] != "sent"
    ]
    if failures:
        raise RuntimeError(f"Email test failed for {len(failures)} recipient(s)")
    return note_path


def send_optimization_preview(summary: dict, recipients: list[str]) -> None:
    choices = summary["choices"]
    text_parts = [
        f"提示词优化第 {summary['round']} 轮",
        f"模型：{summary['model']}",
        f"机器建议：{summary['machine_recommendation']}",
    ]
    html_parts = [
        f"<h1>提示词优化第 {summary['round']} 轮</h1>",
        f"<p><strong>模型：</strong>{escape(summary['model'])}</p>",
        f"<p><strong>机器建议：</strong>{escape(summary['machine_recommendation'])}</p>",
    ]
    for choice, candidate in choices.items():
        output = json.dumps(candidate["outputs"], ensure_ascii=False, indent=2)
        output = output if len(output) <= 6000 else output[:5999] + "…"
        text_parts.extend(
            [
                f"\n[{choice}] {candidate['title']} — {candidate['score']:.2f}/100",
                f"策略：{candidate['strategy']}",
                f"最强项：{candidate['strongest']}",
                f"主要风险：{candidate['risk']}",
                "提示词：\n" + candidate["prompt"],
                "样例输出：\n" + output,
            ]
        )
        html_parts.extend(
            [
                f"<h2>{choice} · {escape(candidate['title'])} — {candidate['score']:.2f}/100</h2>",
                f"<p><strong>策略：</strong>{escape(candidate['strategy'])}</p>",
                f"<p><strong>最强项：</strong>{escape(candidate['strongest'])}</p>",
                f"<p><strong>主要风险：</strong>{escape(candidate['risk'])}</p>",
                f"<h3>提示词</h3><pre>{escape(candidate['prompt'])}</pre>",
                f"<h3>样例输出</h3><pre>{escape(output)}</pre>",
            ]
        )
    text_parts.append("\n邮件仅供预览。请通过 CLI/API 提交选择、反馈或 final。")
    html_parts.append("<p>邮件仅供预览。请通过 CLI/API 提交选择、反馈或 final。</p>")
    results = send_message_email(
        f"提示词优化预览｜第 {summary['round']} 轮",
        "\n".join(text_parts),
        "".join(html_parts),
        f"prompt-optimizer-{summary['session_id']}-r{summary['round']}",
        recipients,
    )
    failures = sum(result["status"] != "sent" for result in results.values())
    if failures:
        raise RuntimeError(f"Optimization preview failed for {failures} recipient(s)")


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
