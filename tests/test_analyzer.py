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


def test_tokenrhythm_uses_compatible_max_tokens(monkeypatch):
    from types import SimpleNamespace

    from yt_finance_kb.analyzer import PoeAnalyzer

    monkeypatch.setattr(
        "yt_finance_kb.analyzer.tiktoken.get_encoding",
        lambda name: SimpleNamespace(encode=lambda text: list(text)),
    )
    analyzer = PoeAnalyzer(
        "poe-key",
        tokenrhythm_api_key="rhythm-key",
        provider_order=("tokenrhythm", "poe"),
    )
    captured = {}

    def fake_create(**request):
        captured.update(request)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":true}'))],
        )

    monkeypatch.setattr(analyzer.tokenrhythm_client.chat.completions, "create", fake_create)
    analyzer._complete(
        [{"role": "user", "content": "return JSON"}],
        requested_max_tokens=800,
        minimum_output_tokens=100,
    )

    assert captured["model"] == "glm-5.2"
    assert captured["max_tokens"] == 800
    assert "max_completion_tokens" not in captured
    assert analyzer.usages[0].provider == "tokenrhythm"


def test_poe_error_falls_back_to_tokenrhythm(monkeypatch):
    from types import SimpleNamespace

    from yt_finance_kb.analyzer import PoeAnalyzer

    monkeypatch.setattr(
        "yt_finance_kb.analyzer.tiktoken.get_encoding",
        lambda name: SimpleNamespace(encode=lambda text: list(text)),
    )
    analyzer = PoeAnalyzer(
        "poe-key",
        tokenrhythm_api_key="rhythm-key",
        provider_order=("poe", "tokenrhythm"),
    )
    monkeypatch.setattr(
        analyzer.client.chat.completions,
        "create",
        lambda **request: (_ for _ in ()).throw(RuntimeError("quota exhausted")),
    )
    monkeypatch.setattr(
        analyzer.tokenrhythm_client.chat.completions,
        "create",
        lambda **request: SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":true}'))],
        ),
    )

    assert analyzer._complete(
        [{"role": "user", "content": "return JSON"}],
        requested_max_tokens=800,
        minimum_output_tokens=100,
    ) == '{"ok":true}'


def test_tokenrhythm_error_falls_back_to_poe(monkeypatch):
    from types import SimpleNamespace

    from yt_finance_kb.analyzer import PoeAnalyzer

    monkeypatch.setattr(
        "yt_finance_kb.analyzer.tiktoken.get_encoding",
        lambda name: SimpleNamespace(encode=lambda text: list(text)),
    )
    analyzer = PoeAnalyzer(
        "poe-key",
        tokenrhythm_api_key="rhythm-key",
        provider_order=("tokenrhythm", "poe"),
    )
    monkeypatch.setattr(
        analyzer.tokenrhythm_client.chat.completions,
        "create",
        lambda **request: (_ for _ in ()).throw(RuntimeError("upstream unavailable")),
    )
    monkeypatch.setattr(
        analyzer.client.chat.completions,
        "create",
        lambda **request: SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":true}'))],
        ),
    )

    assert analyzer._complete(
        [{"role": "user", "content": "return JSON"}],
        requested_max_tokens=800,
        minimum_output_tokens=100,
    ) == '{"ok":true}'


def test_poe_local_budget_exhaustion_falls_back_to_tokenrhythm(monkeypatch):
    from types import SimpleNamespace

    from yt_finance_kb.analyzer import PoeAnalyzer, PoePointBudget

    monkeypatch.setattr(
        "yt_finance_kb.analyzer.tiktoken.get_encoding",
        lambda name: SimpleNamespace(encode=lambda text: list(text)),
    )
    analyzer = PoeAnalyzer(
        "poe-key",
        budget=PoePointBudget(10_000, spent=9_999),
        tokenrhythm_api_key="rhythm-key",
        provider_order=("poe", "tokenrhythm"),
    )
    monkeypatch.setattr(
        analyzer.tokenrhythm_client.chat.completions,
        "create",
        lambda **request: SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":true}'))],
        ),
    )

    assert analyzer._complete(
        [{"role": "user", "content": "return JSON"}],
        requested_max_tokens=800,
        minimum_output_tokens=100,
    ) == '{"ok":true}'
