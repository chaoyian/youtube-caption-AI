from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ChannelConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    url: HttpUrl
    youtube_channel_id: str | None = Field(default=None, pattern=r"^UC[\w-]+$")
    enabled: bool = True
    languages: list[str] = Field(default_factory=lambda: ["zh-TW", "zh-Hant", "zh", "en"])
    backfill_days: int = Field(default=7, ge=0, le=3650)
    min_duration_seconds: int = Field(default=0, ge=0, le=86400)
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
    duration_seconds: int | None = Field(default=None, ge=0)


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0)
    duration: float = Field(default=0, ge=0)
    text: str


SourceType = Literal["主持人陈述", "模型归纳", "模型推导"]


class TimedPoint(BaseModel):
    text: str = Field(min_length=2)
    timestamp: int = Field(ge=0)
    source_type: SourceType


class ExtractedClaim(BaseModel):
    claim: str = Field(min_length=2)
    evidence: list[str] = Field(default_factory=list, max_length=3)
    causal_chain: str | None = None
    conditions: list[str] = Field(default_factory=list, max_length=3)
    risks: list[str] = Field(default_factory=list, max_length=3)
    timestamp: int = Field(ge=0)
    source_type: SourceType


class ChunkExtraction(BaseModel):
    claims: list[ExtractedClaim]


class KnowledgeCard(BaseModel):
    title: str
    insight: str
    timestamp: int = Field(ge=0)
    source_type: SourceType
    topics: list[str] = Field(min_length=1, max_length=3)


class ResearchNote(BaseModel):
    summary: str = Field(min_length=40, max_length=600)
    macro_context: list[TimedPoint] = Field(max_length=3)
    core_theses: list[TimedPoint] = Field(min_length=3, max_length=5)
    evidence: list[TimedPoint] = Field(max_length=6)
    bull_case: list[TimedPoint] = Field(max_length=3)
    bear_case: list[TimedPoint] = Field(max_length=3)
    risks: list[TimedPoint] = Field(max_length=5)
    time_sensitive: list[TimedPoint] = Field(max_length=3)
    cards: list[KnowledgeCard] = Field(min_length=5, max_length=8)
    disclaimer: str
