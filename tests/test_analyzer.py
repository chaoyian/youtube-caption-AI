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
    assert captured["max_tokens"] == 800 + 12_800
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


def test_tokenrhythm_retries_504_before_falling_back(monkeypatch):
    from types import SimpleNamespace

    import httpx
    from openai import InternalServerError

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
    attempts = 0
    delays = []

    def fake_create(**request):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            response = httpx.Response(
                504,
                request=httpx.Request("POST", "https://tokenrhythm.studio/v1/chat/completions"),
            )
            raise InternalServerError("gateway timeout", response=response, body=None)
        return SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":true}'))],
        )

    monkeypatch.setattr(analyzer.tokenrhythm_client.chat.completions, "create", fake_create)
    monkeypatch.setattr("yt_finance_kb.analyzer.time.sleep", delays.append)

    assert analyzer._complete(
        [{"role": "user", "content": "return JSON"}],
        requested_max_tokens=800,
        minimum_output_tokens=100,
    ) == '{"ok":true}'
    assert attempts == 3
    assert delays == [1.0, 2.0]


def test_tokenrhythm_stops_after_three_transport_attempts(monkeypatch):
    from types import SimpleNamespace

    import httpx
    import pytest
    from openai import APITimeoutError

    from yt_finance_kb.analyzer import PoeAnalyzer

    monkeypatch.setattr(
        "yt_finance_kb.analyzer.tiktoken.get_encoding",
        lambda name: SimpleNamespace(encode=lambda text: list(text)),
    )
    analyzer = PoeAnalyzer(None, tokenrhythm_api_key="test-key")
    calls = []
    delays = []

    def timeout(**request):
        calls.append(request)
        raise APITimeoutError(request=httpx.Request("POST", "https://example.com"))

    monkeypatch.setattr(analyzer.tokenrhythm_client.chat.completions, "create", timeout)
    monkeypatch.setattr("yt_finance_kb.analyzer.time.sleep", delays.append)
    with pytest.raises(RuntimeError, match="APITimeoutError"):
        analyzer._complete(
            [{"role": "user", "content": "return JSON"}],
            requested_max_tokens=800,
            minimum_output_tokens=100,
        )
    assert len(calls) == 3
    assert delays == [1.0, 2.0]
    assert analyzer.tokenrhythm_client.max_retries == 0


def test_tokenrhythm_does_not_retry_non_transient_error(monkeypatch):
    from types import SimpleNamespace

    import httpx
    from openai import BadRequestError

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
    attempts = 0

    def fail_tokenrhythm(**request):
        nonlocal attempts
        attempts += 1
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "https://tokenrhythm.studio/v1/chat/completions"),
        )
        raise BadRequestError("bad request", response=response, body=None)

    monkeypatch.setattr(analyzer.tokenrhythm_client.chat.completions, "create", fail_tokenrhythm)
    monkeypatch.setattr(
        analyzer.client.chat.completions,
        "create",
        lambda **request: SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":true}'))],
        ),
    )
    monkeypatch.setattr(
        "yt_finance_kb.analyzer.time.sleep",
        lambda delay: (_ for _ in ()).throw(AssertionError("unexpected retry")),
    )

    assert analyzer._complete(
        [{"role": "user", "content": "return JSON"}],
        requested_max_tokens=800,
        minimum_output_tokens=100,
    ) == '{"ok":true}'
    assert attempts == 1


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
