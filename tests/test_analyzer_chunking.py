import pytest

from yt_finance_kb.analyzer import (
    CHUNK_SIZE,
    PoeAnalyzer,
    PoeBudgetExceeded,
    PoePointBudget,
)


def test_chunking_preserves_timestamped_lines():
    lines = [f"[{index}] 金融观点 {index} " + ("内容" * 100) for index in range(400)]
    transcript = "\n".join(lines)
    chunks = PoeAnalyzer._chunks(transcript)
    assert len(chunks) > 1
    assert all(len(chunk) <= CHUNK_SIZE + len(lines[0]) for chunk in chunks)
    assert "\n".join(chunks) == transcript


def test_point_budget_caps_output_before_call():
    budget = PoePointBudget(10_000)
    maximum = budget.max_output_tokens(
        estimated_prompt_tokens=110_000,
        input_points_per_1k=75,
        output_points_per_1k=450,
        requested=3_200,
        minimum=1_400,
    )
    assert 1_400 <= maximum < 3_200


def test_point_budget_refuses_call_that_cannot_fit():
    budget = PoePointBudget(10_000, spent=9_500)
    with pytest.raises(PoeBudgetExceeded):
        budget.max_output_tokens(
            estimated_prompt_tokens=1_000,
            input_points_per_1k=75,
            output_points_per_1k=450,
            requested=3_200,
            minimum=1_400,
        )


def test_point_budget_tracks_actual_usage():
    budget = PoePointBudget(10_000)
    usage = budget.record("GPT-5.4", 40_000, 2_000, 75, 450)
    assert usage.points == 3_900
    assert budget.remaining == 6_100
