from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from html import unescape
from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig
from yt_dlp import YoutubeDL

from .models import TranscriptSegment


class TranscriptUnavailable(RuntimeError):
    pass


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
    for fetcher in (fetch_with_ytdlp, fetch_with_transcript_api):
        try:
            return fetcher(video_id, languages)
        except Exception as error:
            errors.append(f"{fetcher.__name__}: {type(error).__name__}: {error}")
    raise TranscriptUnavailable(" | ".join(errors))
