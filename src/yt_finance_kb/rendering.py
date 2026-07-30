from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import ChannelConfig, ResearchNote, TimedPoint, Video

MAX_INDEX_TOPICS = 12


def timestamp_link(video_id: str, seconds: int) -> str:
    return f"https://youtu.be/{video_id}?t={seconds}"


def _points(title: str, points: Iterable[TimedPoint], video_id: str) -> list[str]:
    values = list(points)
    lines = [f"## {title}", ""]
    if not values:
        return lines + ["无。", ""]
    for point in values:
        lines.append(
            f"- {point.text} ([{point.timestamp // 60:02d}:{point.timestamp % 60:02d}]"
            f"({timestamp_link(video_id, point.timestamp)})) `[{point.source_type}]`"
        )
    return lines + [""]


def note_topics(note: ResearchNote, channel: ChannelConfig) -> list[str]:
    values: list[str] = []
    for topic in [*channel.tags, *(topic for card in note.cards for topic in card.topics)]:
        if topic not in values:
            values.append(topic)
        if len(values) == MAX_INDEX_TOPICS:
            break
    return values


def render_note(video: Video, channel: ChannelConfig, note: ResearchNote, version: int) -> str:
    topics = note_topics(note, channel)
    entity_names = sorted({entity.name for entity in note.entities})
    date = video.published_at.date().isoformat()
    lines = [
        "---",
        f'title: "{video.title.replace(chr(34), chr(39))}"',
        f"video_id: {video.id}",
        f"channel: {channel.id}",
        f"published: {date}",
        f"version: {version}",
        "topics:",
        *[f'  - "{value}"' for value in topics],
        "entities:",
        *[f'  - "{value}"' for value in entity_names],
        "---",
        "",
        f"# {video.title}",
        "",
        f"- 视频：[YouTube]({video.url})",
        f"- 发布日期：{date}",
        f"- 笔记版本：{version}",
        "",
        "## 金融摘要",
        "",
        note.summary,
        "",
    ]
    lines.extend(_points("宏观与市场背景", note.macro_context, video.id))
    lines.extend(_points("核心判断", note.core_theses, video.id))
    lines.extend(_points("关键依据", note.evidence, video.id))
    lines.extend(_points("多方逻辑", note.bull_case, video.id))
    lines.extend(_points("空方与反例", note.bear_case, video.id))
    lines.extend(_points("风险与不确定性", note.risks, video.id))
    lines.extend(_points("时效性判断", note.time_sensitive, video.id))
    lines.extend(["## 涉及实体", ""])
    if note.entities:
        for entity in note.entities:
            ticker = f"（{entity.ticker}）" if entity.ticker else ""
            lines.append(f"- {entity.name}{ticker}：{entity.type}")
    else:
        lines.append("无。")
    lines.extend(["", "## 原子知识卡片", ""])
    for index, card in enumerate(note.cards, 1):
        topics_text = "、".join(card.topics)
        lines.extend(
            [
                f"### {index}. {card.title}",
                "",
                card.insight,
                "",
                f"- 来源类型：`{card.source_type}`",
                f"- 主题：{topics_text}",
                f"- 时间戳：[{card.timestamp // 60:02d}:{card.timestamp % 60:02d}]"
                f"({timestamp_link(video.id, card.timestamp)})",
                "",
            ]
        )
    lines.extend(["## 说明", "", note.disclaimer, ""])
    return "\n".join(lines)


def note_path(root: Path, channel_id: str, published_at: datetime, video_id: str) -> Path:
    date = published_at.date().isoformat()
    return root / "knowledge" / channel_id / str(published_at.year) / f"{date}-{video_id}.md"


def safe_slug(value: str) -> str:
    slug = re.sub(r"[\s/\\:]+", "-", value.strip().lower())
    slug = re.sub(r"[^\w\u4e00-\u9fff-]", "", slug)
    return slug[:80] or "unknown"


def rebuild_indexes(root: Path, records: dict[str, dict]) -> None:
    topic_map: dict[str, list[dict]] = {}
    entity_map: dict[str, list[dict]] = {}
    for record in records.values():
        if record.get("analysis_status") != "complete" or not record.get("note_path"):
            continue
        item = {
            "title": record["title"],
            "published": record["published_at"][:10],
            "path": record["note_path"],
        }
        for topic in record.get("topics", []):
            topic_map.setdefault(topic, []).append(item)
        for entity in record.get("entities", []):
            entity_map.setdefault(entity, []).append(item)
    _write_index_group(root / "indexes" / "topics", "主题", topic_map, root)
    _write_index_group(root / "indexes" / "entities", "实体", entity_map, root)


def _write_index_group(directory: Path, kind: str, mapping: dict[str, list[dict]], root: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    expected: set[Path] = set()
    for name, items in mapping.items():
        path = directory / f"{safe_slug(name)}.md"
        expected.add(path)
        lines = [f"# {kind}：{name}", ""]
        for item in sorted(items, key=lambda value: value["published"], reverse=True):
            target = Path(item["path"])
            relative = Path("..", "..", target)
            lines.append(f"- {item['published']} [{item['title']}]({relative.as_posix()})")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for old_path in directory.glob("*.md"):
        if old_path not in expected:
            old_path.unlink()
