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


def test_analyzer_requests_json_object_mode(monkeypatch):
    from types import SimpleNamespace

    from yt_finance_kb.analyzer import PoeAnalyzer

    monkeypatch.setattr(
        "yt_finance_kb.analyzer.tiktoken.get_encoding",
        lambda name: SimpleNamespace(encode=lambda text: list(text)),
    )
    analyzer = PoeAnalyzer("test-key")
    captured = {}

    def fake_create(**request):
        captured.update(request)
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"ok":true}'),
                )
            ],
        )

    monkeypatch.setattr(analyzer.client.chat.completions, "create", fake_create)
    analyzer._complete(
        [{"role": "user", "content": "return JSON"}],
        requested_max_tokens=800,
        minimum_output_tokens=100,
    )
    assert captured["response_format"] == {"type": "json_object"}
