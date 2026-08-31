from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .analyzer import (
    DEFAULT_TOKENRHYTHM_MODEL,
    MODEL_POINT_RATES,
    PoeAnalyzer,
    PoePointBudget,
)
from .models import ResearchNote, Video


SCHEMA_VERSION = 1
CHOICES = ("A", "B", "C")
DEFAULT_OBJECTIVE = (
    "把带时间戳的财经视频字幕整理为准确、去重、可检索的中文金融研究笔记，"
    "忽略无关娱乐内容，不补充字幕外事实，并明确区分陈述、归纳和推导。"
)
DEFAULT_CRITERIA = (
    "金融事实与时间戳忠实度；核心观点、证据和风险的覆盖；去重与检索价值；"
    "无关内容过滤；遵守固定 ResearchNote JSON 契约。"
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def extract_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[3:]
        stripped = stripped.rsplit("```", 1)[0]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        starts = [index for index in (stripped.find("{"), stripped.find("[")) if index >= 0]
        if not starts:
            raise
        value, _ = json.JSONDecoder().raw_decode(stripped[min(starts) :])
        return value


def normalize_rubric(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("rubric", raw.get("criteria", []))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Rubric must be a non-empty array")
    items = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "name": str(item.get("name", "Criterion")).strip() or "Criterion",
                "weight": max(1, int(item.get("weight", 1))),
                "description": str(item.get("description", "")).strip(),
            }
        )
    if not items:
        raise ValueError("Rubric contained no valid criteria")
    total = sum(item["weight"] for item in items)
    weights = [max(1, round(item["weight"] * 100 / total)) for item in items]
    difference = 100 - sum(weights)
    while difference:
        indexes = sorted(range(len(weights)), key=lambda index: weights[index], reverse=difference < 0)
        changed = False
        for index in indexes:
            step = 1 if difference > 0 else -1
            if weights[index] + step >= 1:
                weights[index] += step
                difference -= step
                changed = True
                if difference == 0:
                    break
        if not changed:
            raise ValueError("Could not normalize rubric weights")
    for item, weight in zip(items, weights):
        item["weight"] = weight
    return items


def normalize_dimension_scores(raw: Any) -> dict[str, float]:
    if isinstance(raw, dict):
        pairs = raw.items()
    elif isinstance(raw, list):
        pairs = (
            (item.get("name", item.get("criterion", "")), item.get("score"))
            for item in raw
            if isinstance(item, dict)
        )
    else:
        return {}
    dimensions: dict[str, float] = {}
    for name, score in pairs:
        if not str(name).strip() or not isinstance(score, (int, float)):
            continue
        dimensions[str(name)] = max(0.0, min(10.0, float(score)))
    return dimensions


@dataclass
class EvalCase:
    path: Path
    video: Video
    transcript: str
    sha256: str

    @property
    def description(self) -> str:
        return f"{self.video.id}: {self.video.title}"


@dataclass
class Candidate:
    id: str
    title: str
    strategy: str
    prompt: str
    outputs: list[dict[str, Any]]
    score: float = 0.0
    rationale: str = ""
    risk: str = ""
    strongest: str = ""
    dimensions: dict[str, float] | None = None


class OptimizationRuntime(Protocol):
    model: str

    @property
    def points_spent(self) -> int: ...

    def create_rubric(self, objective: str, criteria: str) -> list[dict[str, Any]]: ...

    def create_candidates(
        self,
        objective: str,
        rubric: list[dict[str, Any]],
        round_number: int,
        parent: dict[str, Any] | None,
        feedback: str,
    ) -> list[Candidate]: ...

    def run_candidate(self, candidate: Candidate, cases: list[EvalCase]) -> None: ...

    def evaluate(
        self,
        objective: str,
        rubric: list[dict[str, Any]],
        candidates: list[Candidate],
        baseline: dict[str, Any] | None,
    ) -> tuple[list[str], str]: ...


def _case_payload(video: Video, transcript: str) -> bytes:
    return json.dumps(
        {"video": video.model_dump(mode="json"), "transcript": transcript},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")


def load_eval_case(path: Path) -> EvalCase:
    path = path.resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported evaluation case schema in {path}")
    video = Video.model_validate(raw["video"])
    transcript = str(raw.get("transcript", "")).strip()
    if not transcript:
        raise ValueError(f"Evaluation case transcript is empty: {path}")
    digest = hashlib.sha256(_case_payload(video, transcript)).hexdigest()
    return EvalCase(path, video, transcript, digest)


def write_eval_case(path: Path, video: Video, transcript: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "video": video.model_dump(mode="json"),
        "transcript": transcript,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class PoeOptimizationRuntime:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        point_limit: int,
        points_spent: int = 0,
        input_points_per_1k: int | None = None,
        output_points_per_1k: int | None = None,
        tokenrhythm_api_key: str | None = None,
        tokenrhythm_model: str = DEFAULT_TOKENRHYTHM_MODEL,
        provider_order: tuple[str, ...] = ("poe", "tokenrhythm"),
    ) -> None:
        self.model = model
        self.point_limit = point_limit
        self.budget = PoePointBudget(point_limit, points_spent)
        self.input_points_per_1k = input_points_per_1k
        self.output_points_per_1k = output_points_per_1k
        self.api_key = api_key
        self.tokenrhythm_api_key = tokenrhythm_api_key
        self.tokenrhythm_model = tokenrhythm_model
        self.provider_order = provider_order
        self.gateway = PoeAnalyzer(
            api_key,
            model,
            budget=self.budget,
            input_points_per_1k=input_points_per_1k,
            output_points_per_1k=output_points_per_1k,
            tokenrhythm_api_key=tokenrhythm_api_key,
            tokenrhythm_model=tokenrhythm_model,
            provider_order=provider_order,
        )

    @property
    def points_spent(self) -> int:
        return self.budget.spent

    def _json_call(self, prompt: str, max_tokens: int = 2400) -> Any:
        content = self.gateway._complete(
            [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            requested_max_tokens=max_tokens,
            minimum_output_tokens=500,
        )
        try:
            return extract_json(content)
        except (json.JSONDecodeError, ValueError):
            repaired = self.gateway._complete(
                [
                    {
                        "role": "system",
                        "content": "Repair the supplied incomplete or invalid JSON. Return valid JSON only.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "Return a compact valid JSON object preserving the available values. "
                            "Do not add commentary.\n\n" + content
                        ),
                    },
                ],
                requested_max_tokens=max_tokens,
                minimum_output_tokens=500,
            )
            return extract_json(repaired)

    def create_rubric(self, objective: str, criteria: str) -> list[dict[str, Any]]:
        raw = self._json_call(
            f"""Create a compact weighted rubric for a controlled finance-prompt experiment.

Objective: {objective}
Human criteria: {criteria}

Return an array of 4-6 objects with name, weight, and description. Positive integer weights must
total 100. Include factual correctness and instruction adherence. Score outputs, never prompt style.
"""
        )
        return normalize_rubric(raw)

    def create_candidates(
        self,
        objective: str,
        rubric: list[dict[str, Any]],
        round_number: int,
        parent: dict[str, Any] | None,
        feedback: str,
    ) -> list[Candidate]:
        if parent is None:
            mutation = """Create exactly three genuinely different quality-instruction strategies:
1. concise direct specification;
2. structured research workflow with explicit checks;
3. a contrasting decomposition, role-framing, examples, or self-critique strategy."""
        else:
            mutation = f"""Use this selected parent quality prompt:
--- parent ---
{parent['prompt']}
--- end parent ---
Create exactly three mutations: conservative refinement, targeted repair, and exploratory strategy.
Human feedback: {feedback or 'No additional feedback.'}"""
        raw = self._json_call(
            f"""Design reusable quality instructions for this objective:
{objective}

Rubric: {json.dumps(rubric, ensure_ascii=False)}
{mutation}

The fixed ResearchNote JSON schema and output-format instructions are supplied separately at runtime.
Do not redefine fields, output format, model, source transcript, or API settings. Prompts must require
timestamp fidelity, no outside facts, no personalized investment advice, and professional Chinese.
Return {{"candidates":[{{"title":"...","strategy":"...","prompt":"..."}}]}} with exactly three items.
""",
            max_tokens=3600,
        )
        items = raw.get("candidates", []) if isinstance(raw, dict) else []
        if len(items) != 3:
            raise ValueError("Candidate generation must return exactly three candidates")
        candidates = []
        for index, item in enumerate(items):
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                raise ValueError("Every candidate must contain a prompt")
            candidates.append(
                Candidate(
                    id=f"r{round_number}c{index + 1}",
                    title=str(item.get("title", f"Candidate {index + 1}")),
                    strategy=str(item.get("strategy", "")),
                    prompt=prompt,
                    outputs=[],
                )
            )
        return candidates

    def run_candidate(self, candidate: Candidate, cases: list[EvalCase]) -> None:
        candidate.outputs = []
        for case in cases:
            analyzer = PoeAnalyzer(
                self.api_key,
                self.model,
                budget=self.budget,
                input_points_per_1k=self.input_points_per_1k,
                output_points_per_1k=self.output_points_per_1k,
                quality_prompt=candidate.prompt,
                tokenrhythm_api_key=self.tokenrhythm_api_key,
                tokenrhythm_model=self.tokenrhythm_model,
                provider_order=self.provider_order,
            )
            note = analyzer.analyze(case.video, case.transcript)
            candidate.outputs.append(
                {
                    "case_sha256": case.sha256,
                    "note": note.model_dump(mode="json"),
                }
            )

    def evaluate(
        self,
        objective: str,
        rubric: list[dict[str, Any]],
        candidates: list[Candidate],
        baseline: dict[str, Any] | None,
    ) -> tuple[list[str], str]:
        evaluated: list[Candidate | dict[str, Any]] = list(candidates)
        if baseline and baseline.get("outputs"):
            evaluated.append(baseline)
        shuffled = list(evaluated)
        random.SystemRandom().shuffle(shuffled)
        by_anon = {f"sample-{index + 1}": item for index, item in enumerate(shuffled)}
        samples = [
            {"id": opaque, "outputs": item.outputs if isinstance(item, Candidate) else item["outputs"]}
            for opaque, item in by_anon.items()
        ]
        raw = self._json_call(
            f"""Evaluate only these anonymous outputs for the stated objective.

Objective: {objective}
Rubric: {json.dumps(rubric, ensure_ascii=False)}
Samples: {json.dumps(samples, ensure_ascii=False)}

For each rubric dimension give only a 0-10 numeric score. Perform a pairwise preference check
internally. Return compact JSON with items containing id, dimensions, rationale, strongest, and risk;
ranking containing every id; and recommendation. Keep rationale under 80 Chinese characters and
strongest/risk under 50 each. Do not include per-dimension prose, quote outputs, or infer prompt identity.
""",
            max_tokens=4800,
        )
        item_map = {str(item.get("id")): item for item in raw.get("items", [])}
        rubric_weights = {item["name"]: item["weight"] for item in rubric}
        for opaque, candidate in by_anon.items():
            item = item_map.get(opaque, {})
            dimensions = normalize_dimension_scores(item.get("dimensions", {}))
            weighted = sum(
                dimensions.get(name, 0.0) * weight / 10 for name, weight in rubric_weights.items()
            )
            if isinstance(candidate, Candidate):
                candidate.score = round(weighted, 2)
                candidate.dimensions = dimensions
                candidate.rationale = str(item.get("rationale", ""))
                candidate.strongest = str(item.get("strongest", ""))
                candidate.risk = str(item.get("risk", ""))
            else:
                candidate["score"] = round(weighted, 2)
                candidate["dimensions"] = dimensions
                candidate["rationale"] = str(item.get("rationale", ""))
                candidate["strongest"] = str(item.get("strongest", ""))
                candidate["risk"] = str(item.get("risk", ""))
        ranking = []
        for opaque in raw.get("ranking", []):
            item = by_anon.get(opaque)
            if item is not None:
                ranking.append(item.id if isinstance(item, Candidate) else str(item["id"]))
        expected = [item.id if isinstance(item, Candidate) else str(item["id"]) for item in evaluated]
        if sorted(ranking) != sorted(expected):
            ranking = sorted(
                expected,
                key=lambda identifier: next(
                    (
                        item.score if isinstance(item, Candidate) else float(item.get("score", 0))
                        for item in evaluated
                        if (item.id if isinstance(item, Candidate) else item["id"]) == identifier
                    ),
                    0,
                ),
                reverse=True,
            )
        return ranking, str(raw.get("recommendation", ""))


def _session_path(root: Path, session_id: str) -> Path:
    return root / ".prompt-optimizer" / session_id / "session.json"


def _save_session(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def load_session(path: Path) -> dict[str, Any]:
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported optimization session schema")
    return state


def _case_refs(root: Path, cases: list[EvalCase]) -> list[dict[str, Any]]:
    refs = []
    for case in cases:
        try:
            stored_path = case.path.relative_to(root).as_posix()
        except ValueError:
            stored_path = str(case.path)
        refs.append(
            {
                "path": stored_path,
                "sha256": case.sha256,
                "characters": len(case.transcript),
                "description": case.description,
            }
        )
    return refs


def _load_session_cases(root: Path, state: dict[str, Any]) -> list[EvalCase]:
    cases = []
    for reference in state["cases"]:
        path = Path(reference["path"])
        if not path.is_absolute():
            path = root / path
        case = load_eval_case(path)
        if case.sha256 != reference["sha256"]:
            raise ValueError(f"Evaluation case changed after session start: {path}")
        cases.append(case)
    return cases


def _round_payload(
    round_number: int,
    candidates: list[Candidate],
    baseline: dict[str, Any] | None,
    ranking: list[str],
    recommendation: str,
) -> dict[str, Any]:
    return {
        "round": round_number,
        "created_at": utc_now(),
        "baseline": baseline["id"] if baseline else None,
        "candidates": [asdict(candidate) for candidate in candidates],
        "machine_ranking": ranking,
        "machine_recommendation": recommendation,
        "selected": None,
        "human_feedback": "",
        "assumed_machine_parent": False,
    }


def _run_round(
    state: dict[str, Any],
    runtime: OptimizationRuntime,
    cases: list[EvalCase],
    parent: dict[str, Any] | None,
    feedback: str,
) -> dict[str, Any]:
    round_number = len(state["rounds"]) + 1
    candidates = runtime.create_candidates(
        state["objective"], state["rubric"], round_number, parent, feedback
    )
    if len(candidates) != 3:
        raise ValueError("Optimization runtime must create exactly three candidates")
    for candidate in candidates:
        runtime.run_candidate(candidate, cases)
        for output in candidate.outputs:
            ResearchNote.model_validate(output["note"])
    ranking, recommendation = runtime.evaluate(
        state["objective"], state["rubric"], candidates, parent
    )
    current_best = max(candidate.score for candidate in candidates)
    previous_best = state.get("best_score")
    if previous_best is not None and current_best - previous_best <= 0.5:
        state["plateau_count"] += 1
    else:
        state["plateau_count"] = 0
    state["best_score"] = max(float(previous_best or 0), current_best)
    state["plateau"] = state["plateau_count"] >= 2
    state["points_spent"] = runtime.points_spent
    return _round_payload(round_number, candidates, parent, ranking, recommendation)


def start_session(
    root: Path,
    case_paths: list[Path],
    runtime: OptimizationRuntime,
    *,
    objective: str = DEFAULT_OBJECTIVE,
    criteria: str = DEFAULT_CRITERIA,
    max_rounds: int = 5,
    point_limit: int = 50_000,
) -> tuple[Path, dict[str, Any]]:
    root = root.resolve()
    if not 1 <= max_rounds <= 5:
        raise ValueError("max_rounds must be between 1 and 5")
    cases = [load_eval_case(path) for path in case_paths]
    if not cases:
        raise ValueError("At least one evaluation case is required")
    session_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "status": "active",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "objective": objective,
        "criteria": criteria,
        "model": runtime.model,
        "point_limit": point_limit,
        "points_spent": runtime.points_spent,
        "max_rounds": max_rounds,
        "cases": _case_refs(root, cases),
        "rubric": runtime.create_rubric(objective, criteria),
        "rounds": [],
        "parent": None,
        "best_score": None,
        "plateau_count": 0,
        "plateau": False,
    }
    state["rounds"].append(_run_round(state, runtime, cases, None, ""))
    path = _session_path(root, session_id)
    _save_session(path, state)
    return path, state


def _candidate_for_choice(round_state: dict[str, Any], choice: str) -> dict[str, Any]:
    normalized = choice.upper()
    if normalized not in CHOICES:
        raise ValueError("choice must be A, B, or C")
    return round_state["candidates"][CHOICES.index(normalized)]


def continue_session(
    root: Path,
    session_path: Path,
    runtime: OptimizationRuntime,
    *,
    choice: str | None = None,
    keep: bool = False,
    feedback: str = "",
    edited_prompt: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    state = load_session(session_path)
    if state["status"] != "active":
        raise ValueError("Only active sessions can continue")
    if len(state["rounds"]) >= state["max_rounds"]:
        raise ValueError("Session reached its round limit; finalize a candidate")
    latest = state["rounds"][-1]
    if keep:
        if state["parent"] is None:
            raise ValueError("There is no previous parent to keep in round 1")
        parent = state["parent"]
    elif choice:
        parent = _candidate_for_choice(latest, choice)
    elif feedback.strip():
        winner_id = latest["machine_ranking"][0]
        parent = next(
            (
                candidate
                for candidate in latest["candidates"]
                if candidate["id"] == winner_id
            ),
            state.get("parent") if (state.get("parent") or {}).get("id") == winner_id else None,
        )
        if parent is None:
            raise ValueError("Machine winner is missing from the session record")
        latest["assumed_machine_parent"] = True
    else:
        raise ValueError("Provide a choice, keep, or written feedback")
    if edited_prompt is not None:
        value = edited_prompt.strip()
        if not value:
            raise ValueError("Edited prompt cannot be empty")
        edited = Candidate(
            id=f"r{latest['round']}-edited",
            title=f"Edited {parent['title']}",
            strategy="User-edited parent",
            prompt=value,
            outputs=[],
        )
        runtime.run_candidate(edited, _load_session_cases(root, state))
        parent = asdict(edited)
    latest["selected"] = parent["id"]
    latest["human_feedback"] = feedback.strip()
    state["parent"] = parent
    cases = _load_session_cases(root, state)
    state["rounds"].append(_run_round(state, runtime, cases, parent, feedback.strip()))
    state["points_spent"] = runtime.points_spent
    _save_session(session_path, state)
    return state


def finalize_session(
    root: Path,
    session_path: Path,
    *,
    choice: str | None = None,
    keep: bool = False,
) -> tuple[Path, dict[str, Any]]:
    root = root.resolve()
    state = load_session(session_path)
    if state["status"] != "active":
        raise ValueError("Session is already finalized")
    latest = state["rounds"][-1]
    if keep:
        winner = state.get("parent")
        if winner is None:
            raise ValueError("There is no selected parent to finalize")
    elif choice:
        winner = _candidate_for_choice(latest, choice)
    else:
        raise ValueError("Finalize requires an explicit A/B/C choice or keep")
    latest["selected"] = winner["id"]
    state["parent"] = winner
    state["status"] = "finalized"
    state["finalized_at"] = utc_now()
    state["winner"] = winner

    prompt_dir = root / "prompts" / "finance-note"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    production_path = root / "src" / "yt_finance_kb" / "prompt_assets" / "finance_quality.txt"
    history_dir = prompt_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    if production_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(production_path, history_dir / f"finance_quality-{stamp}.txt")
    _atomic_write_text(production_path, winner["prompt"].strip() + "\n")

    case_scope = ", ".join(reference["description"] for reference in state["cases"])
    prompt_markdown = f"""# Finance Note Quality Prompt

## Purpose

{state['objective']}

## Input contract

The runtime supplies a video title, URL, and timestamped transcript. The fixed `ResearchNote` JSON
schema and output instructions are maintained separately and were not varied by this experiment.

## Final prompt

````text
{winner['prompt'].strip()}
````

## Usage

Production analysis loads this prompt from `src/yt_finance_kb/prompt_assets/finance_quality.txt`.
Start a new controlled experiment with `python -m yt_finance_kb prompt-optimize start`.

## Best-observed evaluation

- Model: `{state['model']}`
- Score: {float(winner.get('score', 0)):.2f}/100
- Strongest quality: {winner.get('strongest', '') or 'Not recorded'}
- Evidence: {winner.get('rationale', '') or 'Not recorded'}
- Remaining risk: {winner.get('risk', '') or 'Not recorded'}
- Tested cases: {case_scope}

This is the best observed prompt selected by the user from the tested candidates and cases, not a
claim of global optimality.
"""
    _atomic_write_text(prompt_dir / "PROMPT.md", prompt_markdown)
    _atomic_write_text(
        prompt_dir / "optimization-log.json",
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
    )
    _save_session(session_path, state)
    return prompt_dir, state


def session_summary(state: dict[str, Any]) -> dict[str, Any]:
    latest = state["rounds"][-1]
    return {
        "schema_version": state["schema_version"],
        "session_id": state["session_id"],
        "status": state["status"],
        "model": state["model"],
        "round": latest["round"],
        "max_rounds": state["max_rounds"],
        "points_spent": state["points_spent"],
        "point_limit": state["point_limit"],
        "plateau": state["plateau"],
        "rubric": state["rubric"],
        "choices": {
            choice: {
                "id": candidate["id"],
                "title": candidate["title"],
                "strategy": candidate["strategy"],
                "prompt": candidate["prompt"],
                "outputs": candidate["outputs"],
                "score": candidate["score"],
                "strongest": candidate.get("strongest", ""),
                "risk": candidate.get("risk", ""),
                "rationale": candidate.get("rationale", ""),
            }
            for choice, candidate in zip(CHOICES, latest["candidates"])
        },
        "machine_recommendation": latest["machine_recommendation"],
        "machine_ranking": latest["machine_ranking"],
    }


def runtime_from_environment(state: dict[str, Any] | None = None, **overrides: Any) -> PoeOptimizationRuntime:
    api_key = os.environ.get("POE_API_KEY")
    tokenrhythm_api_key = os.environ.get("TOKENRHYTHM_API_KEY")
    if not api_key and not tokenrhythm_api_key:
        raise RuntimeError("POE_API_KEY or TOKENRHYTHM_API_KEY is required for prompt optimization")
    model = overrides.get("model") or (state or {}).get("model") or os.environ.get("POE_MODEL", "GPT-5.4")
    point_limit = int(
        overrides.get("point_limit")
        or (state or {}).get("point_limit")
        or os.environ.get("POE_OPTIMIZER_POINT_LIMIT", "50000")
    )
    spent = int((state or {}).get("points_spent", 0))
    input_rate = os.environ.get("POE_INPUT_POINTS_PER_1K")
    output_rate = os.environ.get("POE_OUTPUT_POINTS_PER_1K")
    if api_key and model.lower() not in MODEL_POINT_RATES and not (input_rate and output_rate):
        raise ValueError(f"Configure point rates for unknown Poe model {model!r}")
    return PoeOptimizationRuntime(
        api_key,
        model,
        point_limit=point_limit,
        points_spent=spent,
        input_points_per_1k=int(input_rate) if input_rate else None,
        output_points_per_1k=int(output_rate) if output_rate else None,
        tokenrhythm_api_key=tokenrhythm_api_key,
        tokenrhythm_model=os.environ.get("TOKENRHYTHM_MODEL", DEFAULT_TOKENRHYTHM_MODEL),
        provider_order=tuple(
            item.strip().lower()
            for item in os.environ.get("AI_PROVIDER_ORDER", "poe,tokenrhythm").split(",")
            if item.strip()
        ),
    )
