import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "workstation_start", Path(__file__).resolve().parents[2] / "scripts/start_demo.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_missing_model_file_keeps_existing_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTRACT_ONLY", "preserved")
    assert launcher.model_environment(tmp_path / "missing.json")["CONTRACT_ONLY"] == "preserved"


def test_model_file_overrides_only_allowed_model_settings(tmp_path):
    path = tmp_path / "model.json"
    values = {"CHEMO_PRODUCT_MODEL_ENABLED": "true", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"}
    path.write_text(json.dumps(values))
    env = launcher.model_environment(path)
    assert all(env[key] == value for key, value in values.items())


@pytest.mark.parametrize(
    "values",
    [{"CHEMO_PRODUCT_DATABASE_URL": "CONTRACT_ONLY"}, {"CHEMO_PRODUCT_MODEL_ENABLED": True}, []],
)
def test_model_file_cannot_override_database_or_accept_invalid_values(tmp_path, values):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(values))
    with pytest.raises(RuntimeError, match="不允许数据库或身份配置"):
        launcher.model_environment(path)


def test_backend_restart_waits_before_checking_existing_frontend_proxy(monkeypatch):
    states = iter([False, True])
    waits = []
    monkeypatch.setattr(launcher, "same_workspace", lambda origin: next(states))
    monkeypatch.setattr(launcher, "processes", [])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: 0)
    monkeypatch.setattr(launcher.time, "sleep", waits.append)
    launcher.wait_for_backend(60)
    assert waits == [0.25]
