from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from .models import ChannelConfig, Video

USER_AGENT = "youtube-finance-kb/0.2"
ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"
YOUTUBE_DATA_API = "https://www.googleapis.com/youtube/v3"
ISO_8601_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$"
)


def _is_members_only(title: str) -> bool:
    return bool(re.search(r"會員專屬|会员专属|members?[\s-]+only", title, re.IGNORECASE))


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt, delay in enumerate((0, 2, 6)):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            if attempt == 2 or error.code not in {404, 429, 500, 502, 503, 504}:
                raise
    raise RuntimeError("unreachable")


def _supadata_get(path: str, query: dict[str, str | int]) -> dict:
    api_key = os.environ.get("SUPADATA_API_KEY")
    if not api_key:
        raise RuntimeError("SUPADATA_API_KEY is required when the YouTube RSS feed is unavailable")
    request = urllib.request.Request(
        f"https://api.supadata.ai/v1/{path}?{urlencode(query)}",
        headers={
            "User-Agent": USER_AGENT,
            "x-api-key": api_key,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def _youtube_api_get(path: str, query: dict[str, str | int]) -> dict:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured")
    request = urllib.request.Request(
        f"{YOUTUBE_DATA_API}/{path}?{urlencode({**query, 'key': api_key})}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read())


def _duration_seconds(value: object) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    if ":" in value:
        parts = value.split(":")
        if all(part.isdigit() for part in parts) and 2 <= len(parts) <= 3:
            total = 0
            for part in parts:
                total = total * 60 + int(part)
            return total
    match = ISO_8601_DURATION.fullmatch(value)
    if not match:
        return None
    return int(
        int(match.group("days") or 0) * 86400
        + int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + float(match.group("seconds") or 0)
    )


def _youtube_durations(video_ids: list[str]) -> dict[str, int]:
    if not video_ids:
        return {}
    document = _youtube_api_get(
        "videos",
        {"part": "contentDetails", "id": ",".join(video_ids)},
    )
    durations: dict[str, int] = {}
    for item in document.get("items") or []:
        video_id = item.get("id")
        seconds = _duration_seconds((item.get("contentDetails") or {}).get("duration"))
        if video_id and seconds is not None:
            durations[str(video_id)] = seconds
    return durations


def _supadata_duration(metadata: dict) -> int | None:
    for key in ("durationSeconds", "duration", "lengthSeconds"):
        seconds = _duration_seconds(metadata.get(key))
        if seconds is not None:
            return seconds
    return None


def _apply_duration_filter(channel: ChannelConfig, videos: list[Video]) -> list[Video]:
    minimum = channel.min_duration_seconds
    if minimum <= 0 or not videos:
        return videos

    missing = [video.id for video in videos if video.duration_seconds is None]
    if missing and os.environ.get("YOUTUBE_API_KEY"):
        try:
            durations = _youtube_durations(missing)
        except Exception:
            durations = {}
        for video in videos:
            if video.duration_seconds is None and video.id in durations:
                video.duration_seconds = durations[video.id]

    if os.environ.get("SUPADATA_API_KEY"):
        for video in videos:
            if video.duration_seconds is not None:
                continue
            try:
                metadata = _supadata_get("youtube/video", {"id": video.id})
            except Exception:
                continue
            video.duration_seconds = _supadata_duration(metadata)

    return [
        video
        for video in videos
        if video.duration_seconds is not None and video.duration_seconds >= minimum
    ]


def resolve_channel_id(url: str) -> str:
    match = re.search(r"/channel/(UC[\w-]+)", url)
    if match:
        return match.group(1)
    html = _get(url).decode("utf-8", "ignore")
    patterns = [
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]+)"',
        r'"channelId":"(UC[\w-]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    raise ValueError(f"Unable to resolve YouTube channel id from {url}")


def fetch_channel_videos(
    channel: ChannelConfig,
    backfill_days: int | None = None,
    include_ids: set[str] | None = None,
) -> list[Video]:
    youtube_channel_id = channel.youtube_channel_id or resolve_channel_id(str(channel.url))
    cutoff = datetime.now(UTC) - timedelta(days=channel.backfill_days if backfill_days is None else backfill_days)
    youtube_api_error: Exception | None = None
    if os.environ.get("YOUTUBE_API_KEY"):
        try:
            return _fetch_channel_videos_with_youtube_api(
                channel, youtube_channel_id, cutoff, include_ids
            )
        except Exception as error:
            youtube_api_error = error

    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={youtube_channel_id}"
    try:
        root = ET.fromstring(_get(feed_url))
    except (HTTPError, URLError, ET.ParseError) as rss_error:
        if os.environ.get("SUPADATA_API_KEY"):
            return _fetch_channel_videos_with_supadata(channel, cutoff, include_ids)
        if youtube_api_error is not None:
            raise RuntimeError(
                "YouTube Data API and RSS discovery both failed: "
                f"{type(youtube_api_error).__name__}: {youtube_api_error} | "
                f"{type(rss_error).__name__}: {rss_error}"
            ) from rss_error
        raise

    videos: list[Video] = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        video_id = entry.findtext(f"{{{YT}}}videoId")
        published = entry.findtext(f"{{{ATOM}}}published")
        title = entry.findtext(f"{{{ATOM}}}title")
        if not video_id or not published or not title:
            continue
        if _is_members_only(title):
            continue
        published_at = datetime.fromisoformat(published)
        if published_at < cutoff and video_id not in (include_ids or set()):
            continue
        videos.append(
            Video(
                id=video_id,
                channel_id=channel.id,
                title=unescape(title),
                published_at=published_at,
                url=f"https://www.youtube.com/watch?v={video_id}",
            )
        )
    return sorted(_apply_duration_filter(channel, videos), key=lambda video: video.published_at)


def _fetch_channel_videos_with_youtube_api(
    channel: ChannelConfig,
    youtube_channel_id: str,
    cutoff: datetime,
    include_ids: set[str] | None,
) -> list[Video]:
    channel_document = _youtube_api_get(
        "channels",
        {"part": "contentDetails", "id": youtube_channel_id},
    )
    channel_items = channel_document.get("items") or []
    if not channel_items:
        raise RuntimeError(f"YouTube Data API returned no channel for {youtube_channel_id}")
    uploads_playlist = (
        channel_items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )
    if not uploads_playlist:
        raise RuntimeError(
            f"YouTube Data API returned no uploads playlist for {youtube_channel_id}"
        )

    document = _youtube_api_get(
        "playlistItems",
        {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": 20,
        },
    )
    videos: list[Video] = []
    for item in document.get("items") or []:
        snippet = item.get("snippet") or {}
        content = item.get("contentDetails") or {}
        video_id = content.get("videoId") or (snippet.get("resourceId") or {}).get(
            "videoId"
        )
        title = str(snippet.get("title") or "")
        published = content.get("videoPublishedAt") or snippet.get("publishedAt")
        if not video_id or not title or not published or _is_members_only(title):
            continue
        published_at = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        if published_at < cutoff and video_id not in (include_ids or set()):
            continue
        videos.append(
            Video(
                id=video_id,
                channel_id=channel.id,
                title=unescape(title),
                published_at=published_at,
                url=f"https://www.youtube.com/watch?v={video_id}",
            )
        )
    if channel.min_duration_seconds > 0:
        durations = _youtube_durations([video.id for video in videos])
        for video in videos:
            video.duration_seconds = durations.get(video.id)
    return sorted(_apply_duration_filter(channel, videos), key=lambda video: video.published_at)


def fetch_video_with_youtube_api(video_id: str, channel: ChannelConfig) -> Video:
    document = _youtube_api_get(
        "videos",
        {"part": "snippet", "id": video_id},
    )
    items = document.get("items") or []
    if not items:
        raise RuntimeError(f"YouTube Data API returned no video for {video_id}")
    snippet = items[0].get("snippet") or {}
    published = snippet.get("publishedAt")
    published_at = (
        datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        if published
        else datetime.now(UTC)
    )
    return Video(
        id=video_id,
        channel_id=channel.id,
        title=unescape(str(snippet.get("title") or video_id)),
        published_at=published_at,
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _fetch_channel_videos_with_supadata(
    channel: ChannelConfig,
    cutoff: datetime,
    include_ids: set[str] | None,
) -> list[Video]:
    listings = [
        _supadata_get(
            "youtube/channel/videos",
            {"id": str(channel.url), "limit": 20, "type": video_type},
        )
        for video_type in ("video", "live")
    ]
    video_ids = list(
        dict.fromkeys(
            [
                *(
                    video_id
                    for listing in listings
                    for video_id in [*listing.get("videoIds", []), *listing.get("liveIds", [])]
                ),
            ]
        )
    )
    videos: list[Video] = []
    for video_id in video_ids:
        # The fallback is used when YouTube RSS is temporarily unavailable.
        # Known videos can wait for a later RSS revision check; querying their
        # metadata again wastes Supadata quota and can hide a genuinely new ID
        # behind a 429 response.
        if video_id in (include_ids or set()):
            continue
        video = fetch_video_with_supadata(video_id, channel)
        title = video.title
        if _is_members_only(title):
            continue
        if video.published_at < cutoff and video_id not in (include_ids or set()):
            continue
        videos.append(video)
    return sorted(_apply_duration_filter(channel, videos), key=lambda video: video.published_at)


def fetch_video_with_supadata(video_id: str, channel: ChannelConfig) -> Video:
    metadata = _supadata_get("youtube/video", {"id": video_id})
    upload_date = metadata.get("uploadDate")
    published_at = (
        datetime.fromisoformat(upload_date.replace("Z", "+00:00"))
        if upload_date
        else datetime.now(UTC)
    )
    return Video(
        id=video_id,
        channel_id=channel.id,
        title=unescape(str(metadata.get("title") or video_id)),
        published_at=published_at,
        url=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=_supadata_duration(metadata),
    )


def video_id_from_url(value: str) -> str:
    patterns = [r"[?&]v=([\w-]{11})", r"youtu\.be/([\w-]{11})", r"shorts/([\w-]{11})"]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    if re.fullmatch(r"[\w-]{11}", value):
        return value
    raise ValueError(f"Unable to parse video id: {value}")
