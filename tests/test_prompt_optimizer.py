import json

import pytest

from yt_finance_kb.prompt_optimizer import (
    Candidate,
    continue_session,
    finalize_session,
    load_eval_case,
    load_session,
    normalize_dimension_scores,
    normalize_rubric,
    session_summary,
    start_session,
    write_eval_case,
)


class FakeRuntime:
    model = "fake-model"

    def __init__(self, note, *, spent=0):
        self.note = note
        self._spent = spent
        self.parents = []

    @property
    def points_spent(self):
        return self._spent

    def create_rubric(self, objective, criteria):
        self._spent += 1
        return normalize_rubric(
            [
                {"name": "正确性", "weight": 60, "description": "忠于字幕"},
                {"name": "遵循指令", "weight": 40, "description": "符合约束"},
            ]
        )

    def create_candidates(self, objective, rubric, round_number, parent, feedback):
        self.parents.append((parent, feedback))
        self._spent += 1
        return [
            Candidate(
                id=f"r{round_number}c{index}",
                title=f"候选 {index}",
                strategy=f"策略 {index}",
                prompt=f"第 {round_number} 轮质量指令 {index}",
                outputs=[],
            )
            for index in range(1, 4)
        ]

    def run_candidate(self, candidate, cases):
        self._spent += 1
        candidate.outputs = [
            {"case_sha256": case.sha256, "note": self.note.model_dump(mode="json")}
            for case in cases
        ]

    def evaluate(self, objective, rubric, candidates, baseline):
        self._spent += 1
        for index, candidate in enumerate(candidates):
            candidate.score = 80 + index
            candidate.strongest = "正确性"
            candidate.risk = "样本较少"
            candidate.rationale = "通过固定案例"
            candidate.dimensions = {"正确性": 8 + index / 10, "遵循指令": 8}
        ranking = [candidate.id for candidate in reversed(candidates)]
        if baseline:
            ranking.append(baseline["id"])
        return ranking, "推荐最高分的新候选"


def _case(tmp_path, sample_video):
    path = tmp_path / "preview-output" / "case.prompt-eval.json"
    write_eval_case(path, sample_video, "[1] 利率上升可能压低科技股估值")
    return path


def _production_prompt(tmp_path):
    path = tmp_path / "src/yt_finance_kb/prompt_assets/finance_quality.txt"
    path.parent.mkdir(parents=True)
    path.write_text("旧版正式提示词\n", encoding="utf-8")
    return path


def test_eval_case_round_trip_and_digest(tmp_path, sample_video):
    path = _case(tmp_path, sample_video)
    loaded = load_eval_case(path)
    assert loaded.video == sample_video
    assert loaded.transcript.startswith("[1]")
    assert len(loaded.sha256) == 64


def test_dimension_scores_accept_mapping_and_object_list():
    assert normalize_dimension_scores({"正确性": 9, "遵循指令": 8.5}) == {
        "正确性": 9.0,
        "遵循指令": 8.5,
    }
    assert normalize_dimension_scores(
        [
            {"name": "正确性", "score": 11, "evidence": "..."},
            {"criterion": "遵循指令", "score": 7},
        ]
    ) == {"正确性": 10.0, "遵循指令": 7.0}


def test_start_uses_exactly_three_candidates_and_does_not_store_transcript(
    tmp_path, sample_video, sample_note
):
    case_path = _case(tmp_path, sample_video)
    runtime = FakeRuntime(sample_note)
    session_path, state = start_session(tmp_path, [case_path], runtime, point_limit=100)

    assert len(state["rounds"][0]["candidates"]) == 3
    expected_hash = load_eval_case(case_path).sha256
    for candidate in state["rounds"][0]["candidates"]:
        assert [output["case_sha256"] for output in candidate["outputs"]] == [expected_hash]
    serialized = session_path.read_text(encoding="utf-8")
    assert "利率上升可能压低科技股估值" not in serialized
    assert state["cases"][0]["sha256"] == expected_hash


def test_continue_honors_human_choice_and_edit_without_promoting(
    tmp_path, sample_video, sample_note
):
    case_path = _case(tmp_path, sample_video)
    production = _production_prompt(tmp_path)
    first_runtime = FakeRuntime(sample_note)
    session_path, _ = start_session(tmp_path, [case_path], first_runtime, point_limit=100)
    next_runtime = FakeRuntime(sample_note, spent=first_runtime.points_spent)

    state = continue_session(
        tmp_path,
        session_path,
        next_runtime,
        choice="B",
        feedback="减少重复",
        edited_prompt="用户编辑的质量指令",
    )

    assert state["rounds"][0]["selected"] == "r1-edited"
    assert state["parent"]["prompt"] == "用户编辑的质量指令"
    assert next_runtime.parents[-1][0]["prompt"] == "用户编辑的质量指令"
    assert production.read_text(encoding="utf-8") == "旧版正式提示词\n"


def test_feedback_without_choice_records_machine_parent_assumption(
    tmp_path, sample_video, sample_note
):
    case_path = _case(tmp_path, sample_video)
    runtime = FakeRuntime(sample_note)
    session_path, _ = start_session(tmp_path, [case_path], runtime, point_limit=100)
    next_runtime = FakeRuntime(sample_note, spent=runtime.points_spent)

    state = continue_session(
        tmp_path, session_path, next_runtime, feedback="加强风险条件"
    )

    assert state["rounds"][0]["assumed_machine_parent"] is True
    assert state["rounds"][0]["selected"] == "r1c3"


def test_finalize_requires_explicit_choice_and_preserves_previous_prompt(
    tmp_path, sample_video, sample_note
):
    case_path = _case(tmp_path, sample_video)
    production = _production_prompt(tmp_path)
    runtime = FakeRuntime(sample_note)
    session_path, _ = start_session(tmp_path, [case_path], runtime, point_limit=100)

    with pytest.raises(ValueError, match="explicit"):
        finalize_session(tmp_path, session_path)
    assert production.read_text(encoding="utf-8") == "旧版正式提示词\n"

    prompt_dir, state = finalize_session(tmp_path, session_path, choice="A")
    assert state["status"] == "finalized"
    assert production.read_text(encoding="utf-8") == "第 1 轮质量指令 1\n"
    assert list((prompt_dir / "history").glob("finance_quality-*.txt"))
    assert json.loads((prompt_dir / "optimization-log.json").read_text())["winner"]["id"] == "r1c1"
    assert "第 1 轮质量指令 1" in (prompt_dir / "PROMPT.md").read_text(encoding="utf-8")


def test_session_summary_exposes_json_safe_choices(tmp_path, sample_video, sample_note):
    session_path, state = start_session(
        tmp_path, [_case(tmp_path, sample_video)], FakeRuntime(sample_note), point_limit=100
    )
    summary = session_summary(load_session(session_path))
    assert set(summary["choices"]) == {"A", "B", "C"}
    json.dumps(summary, ensure_ascii=False)


def test_plateau_is_flagged_after_two_negligible_rounds(tmp_path, sample_video, sample_note):
    session_path, state = start_session(
        tmp_path,
        [_case(tmp_path, sample_video)],
        FakeRuntime(sample_note),
        point_limit=100,
        max_rounds=3,
    )
    state = continue_session(
        tmp_path, session_path, FakeRuntime(sample_note, spent=state["points_spent"]), choice="A"
    )
    assert state["plateau"] is False
    state = continue_session(
        tmp_path, session_path, FakeRuntime(sample_note, spent=state["points_spent"]), choice="A"
    )
    assert state["plateau"] is True
    with pytest.raises(ValueError, match="round limit"):
        continue_session(
            tmp_path,
            session_path,
            FakeRuntime(sample_note, spent=state["points_spent"]),
            choice="A",
        )
