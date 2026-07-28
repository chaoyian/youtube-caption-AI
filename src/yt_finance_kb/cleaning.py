from __future__ import annotations

import hashlib
import re

from .models import TranscriptSegment

STAGE_ONLY = re.compile(r"^\s*[\[(【（].{0,30}[\])】）]\s*$")
HTML = re.compile(r"<[^>]+>")
SPACES = re.compile(r"\s+")
AD_HINTS = (
    "訂閱按讚",
    "订阅点赞",
    "開啟小鈴鐺",
    "开启小铃铛",
    "本節目由",
    "本节目由",
    "優惠碼",
    "优惠码",
)


def clean_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    output: list[TranscriptSegment] = []
    previous = ""
    for segment in segments:
        text = SPACES.sub(" ", HTML.sub("", segment.text)).strip()
        if not text or text == previous or STAGE_ONLY.match(text):
            continue
        if len(text) <= 45 and any(hint in text for hint in AD_HINTS):
            continue
        if len(text) == 1 and not text.isalnum():
            continue
        output.append(TranscriptSegment(start=segment.start, duration=segment.duration, text=text))
        previous = text
    return output


def transcript_text(segments: list[TranscriptSegment]) -> str:
    return "\n".join(f"[{int(segment.start)}] {segment.text}" for segment in segments)


def transcript_hash(segments: list[TranscriptSegment]) -> str:
    normalized = "\n".join(
        f"{int(segment.start)}|{SPACES.sub(' ', segment.text).strip()}" for segment in segments
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

