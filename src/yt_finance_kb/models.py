from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ChannelConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    url: HttpUrl
    enabled: bool = True
    languages: list[str] = Field(default_factory=lambda: ["zh-TW", "zh-Hant", "zh", "en"])
    backfill_days: int = Field(default=7, ge=0, le=3650)
    tags: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    channels: list[ChannelConfig]

    @field_validator("channels")
    @classmethod
    def unique_ids(cls, value: list[ChannelConfig]) -> list[ChannelConfig]:
        ids = [channel.id for channel in value]
        if len(ids) != len(set(ids)):
            raise ValueError("channel ids must be unique")
        return value


class Video(BaseModel):
    id: str
    channel_id: str
    title: str
    published_at: datetime
    url: str


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0)
    duration: float = Field(default=0, ge=0)
    text: str


SourceType = Literal["主持人陈述", "模型归纳", "模型推导"]


class TimedPoint(BaseModel):
    text: str = Field(min_length=2)
    timestamp: int = Field(ge=0)
    source_type: SourceType


class ChunkExtraction(BaseModel):
    points: list[TimedPoint]


class Entity(BaseModel):
    name: str
    type: Literal["公司", "行业", "人物", "股票代码", "资产", "政策", "地区", "其他"]
    ticker: str | None = None


class KnowledgeCard(BaseModel):
    title: str
    insight: str
    timestamp: int = Field(ge=0)
    source_type: SourceType
    topics: list[str] = Field(min_length=1, max_length=6)


class ResearchNote(BaseModel):
    summary: str = Field(min_length=40, max_length=600)
    macro_context: list[TimedPoint]
    core_theses: list[TimedPoint] = Field(min_length=1)
    evidence: list[TimedPoint]
    bull_case: list[TimedPoint]
    bear_case: list[TimedPoint]
    risks: list[TimedPoint]
    time_sensitive: list[TimedPoint]
    entities: list[Entity]
    cards: list[KnowledgeCard] = Field(min_length=5, max_length=12)
    disclaimer: str
