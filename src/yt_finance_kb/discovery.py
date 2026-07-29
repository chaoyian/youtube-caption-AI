from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.error import HTTPError
from urllib.parse import urlencode

from .models import ChannelConfig, Video

USER_AGENT = "youtube-finance-kb/0.1"
ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


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
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={youtube_channel_id}"
    cutoff = datetime.now(UTC) - timedelta(days=channel.backfill_days if backfill_days is None else backfill_days)
    try:
        root = ET.fromstring(_get(feed_url))
    except HTTPError:
        if not os.environ.get("SUPADATA_API_KEY"):
            raise
        return _fetch_channel_videos_with_supadata(channel, cutoff, include_ids)

    videos: list[Video] = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        video_id = entry.findtext(f"{{{YT}}}videoId")
        published = entry.findtext(f"{{{ATOM}}}published")
        title = entry.findtext(f"{{{ATOM}}}title")
        if not video_id or not published or not title:
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
    return sorted(videos, key=lambda video: video.published_at)


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
        metadata = _supadata_get("youtube/video", {"id": video_id})
        title = unescape(str(metadata.get("title") or video_id))
        if re.search(r"會員專屬|会员专属|members?[\s-]+only", title, re.IGNORECASE):
            continue
        upload_date = metadata.get("uploadDate")
        if not upload_date:
            continue
        published_at = datetime.fromisoformat(upload_date.replace("Z", "+00:00"))
        if published_at < cutoff and video_id not in (include_ids or set()):
            continue
        videos.append(
            Video(
                id=video_id,
                channel_id=channel.id,
                title=title,
                published_at=published_at,
                url=f"https://www.youtube.com/watch?v={video_id}",
            )
        )
    return sorted(videos, key=lambda video: video.published_at)


def video_id_from_url(value: str) -> str:
    patterns = [r"[?&]v=([\w-]{11})", r"youtu\.be/([\w-]{11})", r"shorts/([\w-]{11})"]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    if re.fullmatch(r"[\w-]{11}", value):
        return value
    raise ValueError(f"Unable to parse video id: {value}")
