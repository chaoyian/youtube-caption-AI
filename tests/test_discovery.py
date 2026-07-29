from datetime import UTC, datetime

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
