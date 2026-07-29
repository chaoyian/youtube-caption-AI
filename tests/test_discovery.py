from datetime import UTC, datetime
from urllib.error import HTTPError

import yt_finance_kb.discovery as discovery
from yt_finance_kb.models import ChannelConfig


def test_configured_channel_id_skips_handle_resolution(monkeypatch):
    channel = ChannelConfig(
        id="test-channel",
        url="https://www.youtube.com/@test",
        youtube_channel_id="UC0123456789abcdefghijk",
        backfill_days=7,
    )
    requested = []
    published = datetime.now(UTC).isoformat()
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>abcdefghijk</yt:videoId>
    <title>财经视频</title>
    <published>{published}</published>
  </entry>
</feed>""".encode()
    monkeypatch.setattr(
        discovery,
        "resolve_channel_id",
        lambda url: (_ for _ in ()).throw(AssertionError("handle should not be resolved")),
    )
    monkeypatch.setattr(
        discovery,
        "_get",
        lambda url: requested.append(url) or feed,
    )
    videos = discovery.fetch_channel_videos(channel)
    assert requested == [
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC0123456789abcdefghijk"
    ]
    assert videos[0].id == "abcdefghijk"


def test_supadata_fallback_when_youtube_feed_is_unavailable(monkeypatch):
    channel = ChannelConfig(
        id="test-channel",
        url="https://www.youtube.com/@test",
        youtube_channel_id="UC0123456789abcdefghijk",
        backfill_days=7,
    )
    published = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    monkeypatch.setenv("SUPADATA_API_KEY", "test-key")
    monkeypatch.setattr(
        discovery,
        "_get",
        lambda url: (_ for _ in ()).throw(HTTPError(url, 404, "Not Found", {}, None)),
    )
    requests = []

    def fake_supadata(path, query):
        requests.append((path, query))
        if path == "youtube/channel/videos":
            return {"videoIds": [], "liveIds": ["abcdefghijk"], "shortIds": []}
        return {
            "id": "abcdefghijk",
            "title": "今日财经",
            "uploadDate": published,
        }

    monkeypatch.setattr(discovery, "_supadata_get", fake_supadata)
    videos = discovery.fetch_channel_videos(channel)
    assert videos[0].id == "abcdefghijk"
    assert videos[0].title == "今日财经"
    assert requests[0] == (
        "youtube/channel/videos",
        {"id": "https://www.youtube.com/@test", "limit": 30, "type": "all"},
    )
