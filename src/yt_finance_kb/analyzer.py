from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from .models import ChunkExtraction, ResearchNote, Video

SYSTEM_PROMPT = """你是一名严谨的金融研究助理。字幕可能包含黄段子、性暗示、冷笑话、闲聊、口头禅、广告、互动或与金融主题无关的娱乐内容。忽略这些内容，不要引用、解释、总结或将其制作成知识卡片。只保留与宏观经济、金融市场、公司、行业、投资逻辑、政策、数据、风险和资产价格有关的信息。幽默内容只有在其本身表达了实质金融观点时，才提取其中的金融含义，并改写为中性、专业语言。

不要联网补充或假装核实事实。严格区分主持人陈述、模型归纳和模型推导。不得提供个性化投资建议。所有 timestamp 必须来自输入中的 [秒数]，不得编造。只输出 JSON，不要 Markdown。"""

OUTPUT_INSTRUCTIONS = """输出对象必须包含：
summary（100至200个中文字左右）；
macro_context、core_theses、evidence、bull_case、bear_case、risks、time_sensitive（数组，每项包含 text、timestamp、source_type）；
entities（数组，每项包含 name、type、ticker；type 只能是公司、行业、人物、股票代码、资产、政策、地区、其他）；
cards（5至12项，每项包含 title、insight、timestamp、source_type、topics）；
disclaimer。
source_type 只能是：主持人陈述、模型归纳、模型推导。没有内容的非核心数组可以为空。"""

DIRECT_TRANSCRIPT_LIMIT = 40_000
CHUNK_SIZE = 28_000


def _json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Model output is not a JSON object")
    return value


class PoeAnalyzer:
    def __init__(self, api_key: str, model: str = "GPT-5.4") -> None:
        self.client = OpenAI(api_key=api_key, base_url="https://api.poe.com/v1", timeout=180)
        self.model = model

    def _complete(self, messages: list[dict[str, str]]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.1,
            max_completion_tokens=7000,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Poe returned an empty response")
        return content

    @staticmethod
    def _chunks(transcript: str) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        length = 0
        for line in transcript.splitlines():
            if current and length + len(line) + 1 > CHUNK_SIZE:
                chunks.append("\n".join(current))
                current, length = [], 0
            current.append(line)
            length += len(line) + 1
        if current:
            chunks.append("\n".join(current))
        return chunks

    def _extract_long_transcript(self, transcript: str) -> str:
        points = []
        for index, chunk in enumerate(self._chunks(transcript), 1):
            instruction = (
                f"这是长视频字幕的第 {index} 个分块。只提取有实质意义的金融观察，"
                "忽略笑话、黄段子、广告和闲聊。输出 JSON 对象 "
                '{"points":[{"text":"...","timestamp":秒数,'
                '"source_type":"主持人陈述"}]}。没有金融内容时 points 为空。'
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": instruction + "\n\n" + chunk},
            ]
            extraction = None
            last_error: Exception | None = None
            bad_output = ""
            for attempt in range(3):
                if attempt:
                    messages = [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"修复为规定的 JSON 对象，不得增加原分块没有的信息。"
                                f"\n验证错误：{last_error}\n原输出：\n{bad_output}"
                            ),
                        },
                    ]
                bad_output = self._complete(messages)
                try:
                    extraction = ChunkExtraction.model_validate(_json_object(bad_output))
                    break
                except (ValueError, json.JSONDecodeError, ValidationError) as error:
                    last_error = error
            if extraction is None:
                raise RuntimeError(f"Chunk output remained invalid after two repairs: {last_error}")
            points.extend(extraction.points)
        return "\n".join(
            f"[{point.timestamp}] {point.text}（{point.source_type}）" for point in points
        )

    def analyze(self, video: Video, transcript: str) -> ResearchNote:
        if len(transcript) > DIRECT_TRANSCRIPT_LIMIT:
            transcript = self._extract_long_transcript(transcript)
            if not transcript:
                raise RuntimeError("Long transcript contained no extractable financial content")
        prompt = (
            f"视频标题：{video.title}\n视频链接：{video.url}\n\n"
            f"{OUTPUT_INSTRUCTIONS}\n\n带秒数的字幕：\n{transcript}"
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        last_error: Exception | None = None
        bad_output = ""
        for attempt in range(3):
            if attempt:
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"修复下面的输出，使其严格符合要求。不得添加字幕中没有的信息。\n"
                            f"{OUTPUT_INSTRUCTIONS}\n验证错误：{last_error}\n原输出：\n{bad_output}"
                        ),
                    },
                ]
            bad_output = self._complete(messages)
            try:
                return ResearchNote.model_validate(_json_object(bad_output))
            except (ValueError, json.JSONDecodeError, ValidationError) as error:
                last_error = error
        raise RuntimeError(f"Poe output remained invalid after two repair attempts: {last_error}")
