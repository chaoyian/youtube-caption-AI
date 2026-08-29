from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/prompt-optimization.yml")


def test_prompt_workflow_supports_remote_round_lifecycle():
    text = WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    dispatch = parsed[True]["workflow_dispatch"]["inputs"]

    assert dispatch["mode"]["options"] == ["start", "continue", "finalize"]
    assert "previous_run_id" in dispatch
    assert dispatch["decision"]["options"] == ["A", "B", "C", "keep", "machine"]
    assert "prompt_eval_only" not in text
    assert "--prompt-eval-only" in text
    assert "actions/download-artifact@" in text
    assert "prompt-optimizer-session" in text
    assert "prompt: finalize finance note optimization" in text


def test_prompt_workflow_keeps_credentials_out_of_dispatch_inputs():
    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    inputs = parsed[True]["workflow_dispatch"]["inputs"]
    assert not {"poe_api_key", "gmail_app_password", "resend_api_key"} & set(inputs)
