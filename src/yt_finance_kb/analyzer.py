from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

import tiktoken
from openai import OpenAI
from pydantic import ValidationError

from .models import ChunkExtraction, ResearchNote, Video

SYSTEM_PROMPT = """你是一名严谨的金融研究助理。字幕可能包含黄段子、性暗示、冷笑话、闲聊、口头禅、广告、互动或与金融主题无关的娱乐内容。忽略这些内容，不要引用、解释、总结或将其制作成知识卡片。只保留与宏观经济、金融市场、公司、行业、投资逻辑、政策、数据、风险和资产价格有关的信息。幽默内容只有在其本身表达了实质金融观点时，才提取其中的金融含义，并改写为中性、专业语言。

不要联网补充或假装核实事实。严格区分主持人陈述、模型归纳和模型推导。不得提供个性化投资建议。所有 timestamp 必须来自输入中的 [秒数]，不得编造。只输出 JSON，不要 Markdown。"""

OUTPUT_INSTRUCTIONS = """输出紧凑、去重的研究笔记 JSON。对象必须包含：
summary（100至180个中文字）；
macro_context（最多3项）、core_theses（3至5项）、evidence（最多6项）、bull_case（最多3项）、bear_case（最多3项）、risks（最多5项）、time_sensitive（最多3项），每项包含 text、timestamp、source_type；
entities（最多15项，只保留有检索价值的公司、行业、人物、股票代码、资产、政策或地区；每项包含 name、type、ticker）；
cards（5至8项，每项包含 title、insight、timestamp、source_type、topics，topics 最多3个）；
disclaimer。
source_type 只能是：主持人陈述、模型归纳、模型推导。没有内容的非核心数组可以为空。
同一观点只能放在最合适的栏目一次：core_theses 写结论，evidence 只写支持它的数据或事实，bull_case/bear_case 只写条件性多空逻辑，risks 只写失效条件。不要为了填满栏目换句话重复。"""

DIRECT_TRANSCRIPT_LIMIT = 100_000
CHUNK_SIZE = 55_000
DEFAULT_POINT_LIMIT_PER_VIDEO = 10_000
DEFAULT_FINAL_MAX_TOKENS = 3_200
MODEL_FINAL_MAX_TOKENS = {
    # Kimi K3 may spend several thousand completion tokens on internal reasoning
    # before emitting the JSON answer. A 3,200-token cap can therefore return an
    # empty content field with finish_reason="length".
    "kimi-k3": 6_000,
}
MODEL_EXTRA_BODY = {
    # Poe requires model-specific parameters to be nested under extra_body.
    # Kimi K3 always reasons and otherwise defaults to its longest effort.
    "kimi-k3": {"reasoning_effort": "low"},
}
MIN_FINAL_OUTPUT_TOKENS = 1_400
MIN_CHUNK_OUTPUT_TOKENS = 700
POINT_SAFETY_RESERVE = 400

MODEL_POINT_RATES = {
    "gpt-5.4": (75, 450),
    "kimi-k3": (100, 500),
}


class PoeBudgetExceeded(RuntimeError):
    pass


@dataclass
class PoeUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    points: int


class PoePointBudget:
    def __init__(self, limit: int, spent: int = 0) -> None:
        if limit <= 0:
            raise ValueError("Poe point limit per video must be positive")
        self.limit = limit
        self.spent = max(0, spent)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    def max_output_tokens(
        self,
        *,
        estimated_prompt_tokens: int,
        input_points_per_1k: int,
        output_points_per_1k: int,
        requested: int,
        minimum: int,
    ) -> int:
        prompt_points = math.ceil(estimated_prompt_tokens * input_points_per_1k / 1000)
        available = self.remaining - prompt_points - POINT_SAFETY_RESERVE
        allowed = math.floor(max(0, available) * 1000 / output_points_per_1k)
        if allowed < minimum:
            raise PoeBudgetExceeded(
                f"This video's Poe budget has {self.remaining} points left; "
                f"this call needs about {prompt_points + math.ceil(minimum * output_points_per_1k / 1000)}"
            )
        return min(requested, allowed)

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        input_points_per_1k: int,
        output_points_per_1k: int,
    ) -> PoeUsage:
        points = math.ceil(prompt_tokens * input_points_per_1k / 1000) + math.ceil(
            completion_tokens * output_points_per_1k / 1000
        )
        self.spent += points
        return PoeUsage(model, prompt_tokens, completion_tokens, points)


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
    def __init__(
        self,
        api_key: str,
        model: str = "GPT-5.4",
        *,
        budget: PoePointBudget | None = None,
        input_points_per_1k: int | None = None,
        output_points_per_1k: int | None = None,
        aux_model: str | None = None,
        aux_input_points_per_1k: int | None = None,
        aux_output_points_per_1k: int | None = None,
        usage_recorder: Any | None = None,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url="https://api.poe.com/v1", timeout=180)
        self.model = model
        default_rates = MODEL_POINT_RATES.get(model.lower())
        if not default_rates and (input_points_per_1k is None or output_points_per_1k is None):
            raise ValueError(f"Configure point rates for unknown Poe model {model!r}")
        self.rates = (
            input_points_per_1k or default_rates[0],
            output_points_per_1k or default_rates[1],
        )
        self.aux_model = aux_model or None
        self.aux_rates: tuple[int, int] | None = None
        if self.aux_model:
            known_aux_rates = MODEL_POINT_RATES.get(self.aux_model.lower())
            if known_aux_rates:
                self.aux_rates = (
                    aux_input_points_per_1k or known_aux_rates[0],
                    aux_output_points_per_1k or known_aux_rates[1],
                )
            elif aux_input_points_per_1k and aux_output_points_per_1k:
                self.aux_rates = (aux_input_points_per_1k, aux_output_points_per_1k)
            else:
                raise ValueError(
                    "POE_AUX_INPUT_POINTS_PER_1K and POE_AUX_OUTPUT_POINTS_PER_1K "
                    "are required for the configured auxiliary model"
                )
        self.budget = budget or PoePointBudget(DEFAULT_POINT_LIMIT_PER_VIDEO)
        self.usage_recorder = usage_recorder
        self.usages: list[PoeUsage] = []
        self.encoding = tiktoken.get_encoding("o200k_base")

    def _estimated_tokens(self, messages: list[dict[str, str]]) -> int:
        return sum(len(self.encoding.encode(message["content"])) + 8 for message in messages) + 20

    def _complete(
        self,
        messages: list[dict[str, str]],
        *,
        requested_max_tokens: int,
        minimum_output_tokens: int,
        use_aux: bool = False,
    ) -> str:
        model = self.aux_model if use_aux else self.model
        rates = self.aux_rates if use_aux else self.rates
        if not model or not rates:
            raise PoeBudgetExceeded(
                "The transcript is too large for one budgeted call and no auxiliary model is configured"
            )
        max_tokens = self.budget.max_output_tokens(
            estimated_prompt_tokens=self._estimated_tokens(messages),
            input_points_per_1k=rates[0],
            output_points_per_1k=rates[1],
            requested=requested_max_tokens,
            minimum=minimum_output_tokens,
        )
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_completion_tokens=max_tokens,
            extra_body=MODEL_EXTRA_BODY.get(model.lower()),
        )
        if response.usage:
            usage = self.budget.record(
                model,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
                rates[0],
                rates[1],
            )
            self.usages.append(usage)
            if self.usage_recorder:
                self.usage_recorder(usage)
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
        claims = []
        for index, chunk in enumerate(self._chunks(transcript), 1):
            instruction = (
                f"这是长视频字幕的第 {index} 个分块。只提取有实质意义、可用于最终研究笔记的金融主张，"
                "忽略笑话、黄段子、广告、寒暄和重复。保留数字、证据、因果关系、成立条件、反例和风险，"
                "不要把它们压成一句模糊摘要。输出 JSON 对象 "
                '{"claims":[{"claim":"主张","evidence":["数据或证据"],'
                '"causal_chain":"因果链或null","conditions":["前提"],"risks":["风险或反例"],'
                '"timestamp":秒数,"source_type":"主持人陈述"}]}。没有金融内容时 claims 为空。'
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
                bad_output = self._complete(
                    messages,
                    requested_max_tokens=1_600,
                    minimum_output_tokens=MIN_CHUNK_OUTPUT_TOKENS,
                    use_aux=bool(self.aux_model),
                )
                try:
                    extraction = ChunkExtraction.model_validate(_json_object(bad_output))
                    break
                except (ValueError, json.JSONDecodeError, ValidationError) as error:
                    last_error = error
            if extraction is None:
                raise RuntimeError(f"Chunk output remained invalid after two repairs: {last_error}")
            claims.extend(extraction.claims)
        return "\n".join(
            (
                f"[{claim.timestamp}] 主张：{claim.claim}；"
                f"证据：{'；'.join(claim.evidence) or '无明确数据'}；"
                f"因果链：{claim.causal_chain or '未说明'}；"
                f"前提：{'；'.join(claim.conditions) or '未说明'}；"
                f"风险/反例：{'；'.join(claim.risks) or '未说明'}"
                f"（{claim.source_type}）"
            )
            for claim in claims
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
            bad_output = self._complete(
                messages,
                requested_max_tokens=MODEL_FINAL_MAX_TOKENS.get(
                    self.model.lower(), DEFAULT_FINAL_MAX_TOKENS
                ),
                minimum_output_tokens=MIN_FINAL_OUTPUT_TOKENS,
            )
            try:
                return ResearchNote.model_validate(_json_object(bad_output))
            except (ValueError, json.JSONDecodeError, ValidationError) as error:
                last_error = error
        raise RuntimeError(f"Poe output remained invalid after two repair attempts: {last_error}")
