from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig
from yt_dlp import YoutubeDL

from .models import TranscriptSegment


class TranscriptUnavailable(RuntimeError):
    pass


class TranscriptPending(TranscriptUnavailable):
    """The video exists, but a native caption track is not available yet."""


class _QuietYtDlpLogger:
    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


@dataclass(frozen=True)
class TranscriptResult:
    language: str
    is_generated: bool
    source: str
    segments: list[TranscriptSegment]


def _select_track(
    tracks: dict[str, list[dict[str, Any]]], languages: list[str]
) -> tuple[str, dict[str, Any]] | None:
    lowered = {name.lower(): name for name in tracks}
    for requested in languages:
        candidates = [requested, requested.lower(), requested.replace("-", "_")]
        chosen_language = next((lowered[item.lower()] for item in candidates if item.lower() in lowered), None)
        if chosen_language is None:
            prefix = requested.lower().split("-")[0]
            chosen_language = next((name for name in tracks if name.lower().startswith(prefix + "-")), None)
        if chosen_language is None:
            continue
        formats = tracks[chosen_language]
        chosen = next((item for item in formats if item.get("ext") == "json3"), None)
        chosen = chosen or next((item for item in formats if item.get("ext") == "vtt"), None)
        chosen = chosen or (formats[0] if formats else None)
        if chosen:
            return chosen_language, chosen
    return None


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def _supadata_request(url: str, api_key: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "youtube-finance-kb/0.2",
            "x-api-key": api_key,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.status, json.loads(response.read())


def _apify_request(video_id: str, token: str) -> list[dict[str, Any]]:
    actor = os.environ.get(
        "APIFY_TRANSCRIPT_ACTOR", "apihq~youtube-transcript-scraper"
    ).strip()
    if not actor or not re.fullmatch(r"[\w~-]+", actor):
        raise ValueError("APIFY_TRANSCRIPT_ACTOR contains invalid characters")
    body = json.dumps(
        {"videoId": video_id, "metadata": False},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        (
            f"https://api.apify.com/v2/acts/{actor}/"
            "run-sync-get-dataset-items?timeout=90&clean=true"
        ),
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "youtube-finance-kb/0.2",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=105) as response:
        document = json.loads(response.read())
    if not isinstance(document, list):
        raise TranscriptUnavailable("Apify returned an invalid dataset response")
    return document


def fetch_with_apify(video_id: str, languages: list[str]) -> TranscriptResult:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise TranscriptUnavailable("APIFY_TOKEN is not configured")
    rows = _apify_request(video_id, token)
    if not rows:
        raise TranscriptUnavailable("Apify returned no transcript result")
    row = rows[0]
    if not row.get("success"):
        code = str(row.get("code") or "UNKNOWN")
        message = str(row.get("error") or "transcript unavailable")
        exception = (
            TranscriptPending
            if code in {"NO_CAPTIONS", "CAPTIONS_TIMEOUT"}
            else TranscriptUnavailable
        )
        raise exception(f"Apify {code}: {message}")
    content = row.get("transcript")
    if not isinstance(content, list):
        raise TranscriptUnavailable("Apify returned no timestamped transcript")
    segments = [
        TranscriptSegment(
            start=float(item.get("start", 0)),
            duration=float(item.get("duration", 0)),
            text=str(item.get("text", "")).strip(),
        )
        for item in content
        if str(item.get("text", "")).strip()
    ]
    if not segments:
        raise TranscriptUnavailable("Apify returned an empty transcript")
    return TranscriptResult(
        language=str(row.get("language") or (languages[0] if languages else "unknown")),
        is_generated=bool(row.get("is_auto_generated")),
        source="apify",
        segments=segments,
    )


def fetch_with_supadata(video_id: str, languages: list[str]) -> TranscriptResult:
    api_key = os.environ.get("SUPADATA_API_KEY")
    if not api_key:
        raise TranscriptUnavailable("SUPADATA_API_KEY is not configured")
    query = urlencode(
        {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "lang": languages[0] if languages else "zh-TW",
            "text": "false",
            "mode": "native",
        }
    )
    try:
        status, document = _supadata_request(
            f"https://api.supadata.ai/v1/transcript?{query}", api_key
        )
    except HTTPError as error:
        if error.code == 429:
            raise TranscriptPending("Supadata is temporarily rate limited; retry next hour") from error
        raise
    if status == 206 or document.get("error") == "transcript-unavailable":
        raise TranscriptPending("Supadata reports that native captions are not available yet")
    if status == 202 or "jobId" in document:
        job_id = document.get("jobId")
        if not job_id:
            raise TranscriptUnavailable("Supadata returned an asynchronous response without jobId")
        for _ in range(30):
            time.sleep(2)
            _, document = _supadata_request(
                f"https://api.supadata.ai/v1/transcript/{job_id}", api_key
            )
            if document.get("status") == "completed":
                break
            if document.get("status") == "failed":
                raise TranscriptUnavailable(f"Supadata transcript job failed: {document.get('error')}")
        else:
            raise TranscriptUnavailable("Supadata transcript job did not finish within 60 seconds")
    content = document.get("content")
    if not isinstance(content, list):
        raise TranscriptUnavailable("Supadata returned no timestamped transcript")
    segments = [
        TranscriptSegment(
            start=float(item.get("offset", 0)) / 1000,
            duration=float(item.get("duration", 0)) / 1000,
            text=str(item.get("text", "")).strip(),
        )
        for item in content
        if str(item.get("text", "")).strip()
    ]
    if not segments:
        raise TranscriptUnavailable("Supadata returned an empty transcript")
    return TranscriptResult(
        language=str(document.get("lang") or languages[0]),
        is_generated=False,
        source="supadata",
        segments=segments,
    )


def _parse_json3(data: bytes) -> list[TranscriptSegment]:
    document = json.loads(data)
    output: list[TranscriptSegment] = []
    for event in document.get("events", []):
        text = "".join(segment.get("utf8", "") for segment in event.get("segs", []))
        text = unescape(text).replace("\n", " ").strip()
        if not text:
            continue
        output.append(
            TranscriptSegment(
                start=float(event.get("tStartMs", 0)) / 1000,
                duration=float(event.get("dDurationMs", 0)) / 1000,
                text=text,
            )
        )
    return output


_VTT_TIMESTAMP = re.compile(
    r"(?P<h1>\d{2}):(?P<m1>\d{2}):(?P<s1>\d{2})[.,](?P<ms1>\d{3})"
    r"\s+-->\s+"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2})[.,](?P<ms2>\d{3})"
)


def _seconds(match: re.Match[str], suffix: str) -> float:
    return (
        int(match.group("h" + suffix)) * 3600
        + int(match.group("m" + suffix)) * 60
        + int(match.group("s" + suffix))
        + int(match.group("ms" + suffix)) / 1000
    )


def _parse_vtt(data: bytes) -> list[TranscriptSegment]:
    lines = data.decode("utf-8", "ignore").splitlines()
    output: list[TranscriptSegment] = []
    index = 0
    while index < len(lines):
        match = _VTT_TIMESTAMP.search(lines[index])
        if not match:
            index += 1
            continue
        start, end = _seconds(match, "1"), _seconds(match, "2")
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            value = re.sub(r"<[^>]+>", "", lines[index]).strip()
            if value:
                text_lines.append(value)
            index += 1
        text = unescape(" ".join(text_lines)).strip()
        if text:
            output.append(TranscriptSegment(start=start, duration=max(0, end - start), text=text))
    return output


def fetch_with_ytdlp(video_id: str, languages: list[str]) -> TranscriptResult:
    proxy_url = os.environ.get("YOUTUBE_PROXY_URL")
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "socket_timeout": 30,
        "logger": _QuietYtDlpLogger(),
    }
    if proxy_url:
        options["proxy"] = proxy_url
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    manual = _select_track(info.get("subtitles") or {}, languages)
    generated = False
    selected = manual
    if selected is None:
        selected = _select_track(info.get("automatic_captions") or {}, languages)
        generated = True
    if selected is None:
        raise TranscriptUnavailable("yt-dlp found no preferred subtitle track")
    language, track = selected
    payload = _download(track["url"])
    segments = _parse_json3(payload) if track.get("ext") == "json3" else _parse_vtt(payload)
    if not segments:
        raise TranscriptUnavailable("yt-dlp returned an empty subtitle track")
    return TranscriptResult(language, generated, "yt-dlp", segments)


def fetch_with_transcript_api(video_id: str, languages: list[str]) -> TranscriptResult:
    proxy_url = os.environ.get("YOUTUBE_PROXY_URL")
    proxy_config = (
        GenericProxyConfig(http_url=proxy_url, https_url=proxy_url) if proxy_url else None
    )
    api = YouTubeTranscriptApi(proxy_config=proxy_config)
    transcript_list = api.list(video_id)
    try:
        transcript = transcript_list.find_manually_created_transcript(languages)
    except Exception:
        try:
            transcript = transcript_list.find_generated_transcript(languages)
        except Exception as error:
            raise TranscriptUnavailable(f"youtube-transcript-api found no preferred track: {error}") from error
    fetched = transcript.fetch()
    segments = [
        TranscriptSegment(start=item.start, duration=item.duration, text=item.text)
        for item in fetched
        if item.text.strip()
    ]
    if not segments:
        raise TranscriptUnavailable("youtube-transcript-api returned an empty transcript")
    return TranscriptResult(transcript.language_code, transcript.is_generated, "youtube-transcript-api", segments)


def fetch_transcript(video_id: str, languages: list[str]) -> TranscriptResult:
    errors: list[str] = []
    pending = False
    fetchers = [fetch_with_ytdlp, fetch_with_transcript_api]
    if os.environ.get("APIFY_TOKEN"):
        fetchers.append(fetch_with_apify)
    if os.environ.get("SUPADATA_API_KEY"):
        fetchers.append(fetch_with_supadata)
    for fetcher in fetchers:
        try:
            return fetcher(video_id, languages)
        except Exception as error:
            pending = pending or isinstance(error, TranscriptPending)
            errors.append(f"{fetcher.__name__}: {type(error).__name__}: {error}")
    exception = TranscriptPending if pending else TranscriptUnavailable
    raise exception(" | ".join(errors))
