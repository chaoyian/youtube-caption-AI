from yt_finance_kb.analyzer import (
    DEFAULT_FINAL_MAX_TOKENS,
    MODEL_EXTRA_BODY,
    MODEL_FINAL_MAX_TOKENS,
    SYSTEM_PROMPT,
)


def test_system_prompt_explicitly_ignores_non_financial_humor():
    for phrase in ("黄段子", "性暗示", "冷笑话", "金融市场", "风险"):
        assert phrase in SYSTEM_PROMPT


def test_kimi_k3_allows_reasoning_before_json_output():
    assert MODEL_FINAL_MAX_TOKENS["kimi-k3"] > DEFAULT_FINAL_MAX_TOKENS
    assert MODEL_EXTRA_BODY["kimi-k3"] == {"reasoning_effort": "low"}
