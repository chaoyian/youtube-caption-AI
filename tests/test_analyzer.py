from yt_finance_kb.analyzer import SYSTEM_PROMPT


def test_system_prompt_explicitly_ignores_non_financial_humor():
    for phrase in ("黄段子", "性暗示", "冷笑话", "金融市场", "风险"):
        assert phrase in SYSTEM_PROMPT

