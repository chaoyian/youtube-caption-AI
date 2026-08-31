from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from yt_dlp import YoutubeDL

from .analyzer import (
    DEFAULT_POINT_LIMIT_PER_VIDEO,
    DEFAULT_TOKENRHYTHM_MODEL,
    PoeAnalyzer,
    PoePointBudget,
    PoeUsage,
)
from .cleaning import clean_segments, transcript_hash, transcript_text
from .config import load_config
from .discovery import (
    fetch_channel_videos,
    fetch_video_with_supadata,
    fetch_video_with_youtube_api,
    video_id_from_url,
)
from .models import ChannelConfig, Video
from .notifications import (
    configured_email_providers,
    email_recipients,
    read_note,
    send_discord,
    send_discord_alert,
    send_email,
)
from .prompt_optimizer import write_eval_case
from .rendering import note_path, note_topics, rebuild_indexes, render_note
from .state import load_state, now_iso, retry_due, save_state, schedule_retry
from .transcripts import TranscriptPending, fetch_transcript


@dataclass
class ProcessResult:
    discovered: int = 0
    unchanged: int = 0
    analyzed: int = 0
    waiting: int = 0
    failed: int = 0
    poe_calls: int = 0
    poe_points: int = 0


def _manual_video(value: str, channel: ChannelConfig) -> Video:
    video_id = video_id_from_url(value)
    if os.environ.get("YOUTUBE_API_KEY"):
        try:
            return fetch_video_with_youtube_api(video_id, channel)
        except Exception:
            pass
    try:
        with YoutubeDL(
            {"quiet": True, "no_warnings": True, "skip_download": True}
        ) as downloader:
            info = downloader.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
    except Exception:
        if os.environ.get("SUPADATA_API_KEY"):
            return fetch_video_with_supadata(video_id, channel)
        raise
    timestamp = info.get("timestamp")
    published = datetime.fromtimestamp(timestamp, UTC) if timestamp else datetime.now(UTC)
    return Video(
        id=video_id,
        channel_id=channel.id,
        title=info.get("title") or video_id,
        published_at=published,
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _base_record(video: Video) -> dict[str, Any]:
    return {
        "video_id": video.id,
        "channel_id": video.channel_id,
        "title": video.title,
        "published_at": video.published_at.isoformat(),
        "video_url": video.url,
        "fetch_status": "pending",
        "analysis_status": "pending",
        "email_status": "pending",
        "discord_status": "pending",
        "alert_status": "none",
        "failure_count": 0,
        "note_version": 0,
    }


def _record_failure(record: dict[str, Any], stage: str, error: Exception) -> None:
    record[f"{stage}_status"] = "failed"
    record["failure_count"] = int(record.get("failure_count", 0)) + 1
    record["next_retry_at"] = schedule_retry(record["failure_count"])
    record["last_error"] = _safe_error(error)[:1200]
    record["last_attempt_at"] = now_iso()
    record["alert_status"] = "pending"


def _record_waiting(record: dict[str, Any], error: Exception) -> None:
    record["fetch_status"] = "waiting"
    record["analysis_status"] = "pending"
    record["waiting_count"] = int(record.get("waiting_count", 0)) + 1
    record["next_retry_at"] = schedule_retry(hours=1)
    record["last_error"] = _safe_error(error)[:1200]
    record["last_attempt_at"] = now_iso()
    record["alert_status"] = "none"


def _safe_error(error: Exception) -> str:
    message = f"{type(error).__name__}: {error}"
    proxy_url = os.environ.get("YOUTUBE_PROXY_URL")
    parsed = urlsplit(proxy_url) if proxy_url else None
    sensitive_values = [
        os.environ.get("YOUTUBE_API_KEY"),
        os.environ.get("APIFY_TOKEN"),
        os.environ.get("SUPADATA_API_KEY"),
        os.environ.get("POE_API_KEY"),
        os.environ.get("TOKENRHYTHM_API_KEY"),
        os.environ.get("RESEND_API_KEY"),
        os.environ.get("GMAIL_APP_PASSWORD"),
        os.environ.get("DISCORD_WEBHOOK_URL"),
        proxy_url,
        parsed.username if parsed else None,
        parsed.password if parsed else None,
    ]
    for value in sensitive_values:
        if value and len(value) >= 4:
            message = message.replace(value, "***")
            message = message.replace(quote(value, safe=""), "***")
    return message


def _optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else None


def _usage_recorder(record: dict[str, Any], result: ProcessResult):
    def record_usage(usage: PoeUsage) -> None:
        total = record.setdefault(
            "poe_usage",
            {"points": 0, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "models": {}},
        )
        total["points"] += usage.points
        total["prompt_tokens"] += usage.prompt_tokens
        total["completion_tokens"] += usage.completion_tokens
        total["calls"] += 1
        total["models"][usage.model] = total["models"].get(usage.model, 0) + usage.points
        providers = total.setdefault("providers", {})
        providers[usage.provider] = providers.get(usage.provider, 0) + 1
        result.poe_calls += 1
        result.poe_points += usage.points

    return record_usage


def _revision_check_due(record: dict[str, Any]) -> bool:
    last_fetched = record.get("last_fetched_at")
    if not last_fetched:
        return True
    return datetime.fromisoformat(last_fetched) <= datetime.now(UTC) - timedelta(days=7)


def process(
    root: Path,
    *,
    config_path: Path,
    state_path: Path,
    channel_filter: str | None = None,
    video_url: str | None = None,
    backfill_days: int | None = None,
    latest_per_channel: int | None = None,
    preview: bool = False,
    prompt_eval_only: bool = False,
    force: bool = False,
    analyzer: Any | None = None,
) -> ProcessResult:
    config = load_config(config_path)
    state = load_state(state_path)
    records: dict[str, dict[str, Any]] = state["videos"]
    result = ProcessResult()
    channels = [
        channel
        for channel in config.channels
        if channel.enabled and (channel_filter is None or channel.id == channel_filter)
    ]
    if not channels:
        raise ValueError(f"No enabled channel matched {channel_filter!r}")
    if video_url and len(channels) != 1:
        raise ValueError("--video requires exactly one selected channel (use --channel)")
    if video_url and latest_per_channel is not None:
        raise ValueError("--video cannot be combined with --latest-per-channel")
    if latest_per_channel is not None and latest_per_channel < 1:
        raise ValueError("--latest-per-channel must be at least 1")
    if prompt_eval_only and not preview:
        raise ValueError("prompt_eval_only requires preview mode")

    candidates: list[tuple[ChannelConfig, Video]] = []
    for channel in channels:
        if video_url:
            candidates.append((channel, _manual_video(video_url, channel)))
            continue
        known_ids = {
            video_id for video_id, record in records.items() if record.get("channel_id") == channel.id
        }
        channel_videos = fetch_channel_videos(
            channel, backfill_days, include_ids=known_ids
        )
        if latest_per_channel is not None:
            channel_videos = sorted(
                channel_videos, key=lambda candidate: candidate.published_at
            )[-latest_per_channel:]
        candidates.extend((channel, video) for video in channel_videos)
    result.discovered = len(candidates)

    for channel, video in candidates:
        record = records.setdefault(video.id, _base_record(video))
        record.update(
            title=video.title,
            video_url=video.url,
            channel_id=channel.id,
        )
        if not record.get("published_at"):
            record["published_at"] = video.published_at.isoformat()
        if not force and record.get("fetch_status") == "waiting" and not retry_due(record):
            result.waiting += 1
            continue
        if not force and record.get("analysis_status") == "failed" and not retry_due(record):
            continue
        if (
            not force
            and record.get("analysis_status") == "complete"
            and record.get("analyzed_hash")
            and not _revision_check_due(record)
        ):
            result.unchanged += 1
            continue
        try:
            transcript = fetch_transcript(video.id, channel.languages)
            if preview:
                transcript_target = (
                    root / "preview-output" / f"{video.id}.raw-transcript.txt"
                )
                transcript_target.parent.mkdir(parents=True, exist_ok=True)
                transcript_target.write_text(
                    transcript_text(transcript.segments), encoding="utf-8"
                )
            cleaned = clean_segments(transcript.segments)
            if not cleaned:
                raise RuntimeError("Transcript became empty after cleaning")
            if preview:
                write_eval_case(
                    root / "preview-output" / f"{video.id}.prompt-eval.json",
                    video,
                    transcript_text(cleaned),
                )
            content_hash = transcript_hash(cleaned)
            record.pop("entities", None)
            record.update(
                fetch_status="complete",
                transcript_language=transcript.language,
                transcript_source=transcript.source,
                transcript_generated=transcript.is_generated,
                fetched_hash=content_hash,
                last_fetched_at=now_iso(),
                failure_count=0,
                next_retry_at=None,
            )
            if prompt_eval_only:
                continue
        except TranscriptPending as error:
            _record_waiting(record, error)
            result.waiting += 1
            continue
        except Exception as error:
            _record_failure(record, "fetch", error)
            result.failed += 1
            continue

        if not force and record.get("analyzed_hash") == content_hash:
            result.unchanged += 1
            continue
        try:
            active_analyzer = analyzer
            if active_analyzer is None:
                api_key = os.environ.get("POE_API_KEY")
                tokenrhythm_api_key = os.environ.get("TOKENRHYTHM_API_KEY")
                if not api_key and not tokenrhythm_api_key:
                    raise RuntimeError(
                        "POE_API_KEY or TOKENRHYTHM_API_KEY is required for a new or changed transcript"
                    )
                point_limit = int(
                    os.environ.get("POE_POINT_LIMIT_PER_VIDEO", DEFAULT_POINT_LIMIT_PER_VIDEO)
                )
                record["poe_usage"] = {
                    "points": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "calls": 0,
                    "models": {},
                    "limit": point_limit,
                }
                active_analyzer = PoeAnalyzer(
                    api_key,
                    os.environ.get("POE_MODEL", "GPT-5.4"),
                    budget=PoePointBudget(point_limit),
                    input_points_per_1k=_optional_int("POE_INPUT_POINTS_PER_1K"),
                    output_points_per_1k=_optional_int("POE_OUTPUT_POINTS_PER_1K"),
                    aux_model=os.environ.get("POE_AUX_MODEL"),
                    aux_input_points_per_1k=_optional_int("POE_AUX_INPUT_POINTS_PER_1K"),
                    aux_output_points_per_1k=_optional_int("POE_AUX_OUTPUT_POINTS_PER_1K"),
                    usage_recorder=_usage_recorder(record, result),
                    tokenrhythm_api_key=tokenrhythm_api_key,
                    tokenrhythm_model=os.environ.get(
                        "TOKENRHYTHM_MODEL", DEFAULT_TOKENRHYTHM_MODEL
                    ),
                    provider_order=tuple(
                        item.strip().lower()
                        for item in os.environ.get(
                            "AI_PROVIDER_ORDER", "tokenrhythm,poe"
                        ).split(",")
                        if item.strip()
                    ),
                )
            note = active_analyzer.analyze(video, transcript_text(cleaned))
            version = int(record.get("note_version", 0)) + 1
            markdown_body = render_note(video, channel, note, version)
            if preview:
                target = root / "preview-output" / f"{video.id}.md"
            else:
                target = note_path(root, channel.id, video.published_at, video.id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown_body, encoding="utf-8")
            relative_path = target.relative_to(root).as_posix()
            record.update(
                analysis_status="complete",
                analyzed_hash=content_hash,
                analyzed_at=now_iso(),
                note_version=version,
                note_path=relative_path,
                topics=note_topics(note, channel),
                email_status="pending",
                email_deliveries={},
                discord_status="pending",
                alert_status="none",
                last_error=None,
                failure_count=0,
                next_retry_at=None,
            )
            result.analyzed += 1
        except Exception as error:
            _record_failure(record, "analysis", error)
            result.failed += 1

    if not preview:
        rebuild_indexes(root, records)
        save_state(state_path, state)
    return result


def _note_url(repository_url: str, branch: str, note_path_value: str) -> str:
    return f"{repository_url.rstrip('/')}/blob/{branch}/{note_path_value}"


def notify(root: Path, state_path: Path, repository_url: str, branch: str = "main") -> dict[str, int]:
    state = load_state(state_path)
    counts = {"email": 0, "discord": 0, "alerts": 0, "failed": 0}
    for record in state["videos"].values():
        if record.get("alert_status") in {"pending", "failed", "disabled"}:
            if not os.environ.get("DISCORD_WEBHOOK_URL"):
                record["alert_status"] = "disabled"
            else:
                try:
                    send_discord_alert(
                        f"{record.get('title', record['video_id'])} 处理失败：{record.get('last_error', '未知错误')}"
                    )
                    record["alert_status"] = "sent"
                    counts["alerts"] += 1
                except Exception as error:
                    record["alert_status"] = "failed"
                    record["alert_error"] = f"{type(error).__name__}: {error}"[:600]
                    counts["failed"] += 1
            save_state(state_path, state)

        if record.get("analysis_status") != "complete" or not record.get("note_path"):
            continue
        body = read_note(root, record["note_path"])
        url = _note_url(repository_url, branch, record["note_path"])
        if record.get("email_status") in {"pending", "partial", "failed", "disabled"}:
            recipients = email_recipients()
            providers = configured_email_providers()
            if not recipients or not providers:
                record["email_status"] = "disabled"
            else:
                version = int(record["note_version"])
                deliveries = record.setdefault("email_deliveries", {})
                pending = [
                    recipient
                    for recipient in recipients
                    if deliveries.get(recipient, {}).get("status") != "sent"
                    or deliveries.get(recipient, {}).get("version") != version
                ]
                results = (
                    send_email(
                        record["title"],
                        body,
                        url,
                        record["video_url"],
                        f"{record['video_id']}-v{version}",
                        pending,
                    )
                    if pending
                    else {}
                )
                for recipient, delivery in results.items():
                    deliveries[recipient] = {**delivery, "version": version, "attempted_at": now_iso()}
                    if delivery["status"] == "sent":
                        counts["email"] += 1
                sent = sum(
                    deliveries.get(recipient, {}).get("status") == "sent"
                    and deliveries.get(recipient, {}).get("version") == version
                    for recipient in recipients
                )
                if sent == len(recipients):
                    record["email_status"] = "sent"
                    record["email_sent_at"] = now_iso()
                    record.pop("email_error", None)
                elif sent:
                    record["email_status"] = "partial"
                    record["email_error"] = "One or more recipients could not be reached"
                    counts["failed"] += len(recipients) - sent
                else:
                    record["email_status"] = "failed"
                    record["email_error"] = "No recipients could be reached"
                    counts["failed"] += len(recipients)
            save_state(state_path, state)
        if record.get("discord_status") in {"pending", "failed", "disabled"}:
            if not os.environ.get("DISCORD_WEBHOOK_URL"):
                record["discord_status"] = "disabled"
            else:
                try:
                    send_discord(record["title"], body, url, record["video_url"])
                    record["discord_status"] = "sent"
                    record["discord_sent_at"] = now_iso()
                    counts["discord"] += 1
                except Exception as error:
                    record["discord_status"] = "failed"
                    record["discord_error"] = f"{type(error).__name__}: {error}"[:600]
                    counts["failed"] += 1
            save_state(state_path, state)
    return counts
