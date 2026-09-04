import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "custom-multi-agent" / "scripts"))
import init_task as init_module


def test_cost_tracker_template_schema():
    tracker = json.loads(
        (ROOT / ".agents" / "skills" / "custom-multi-agent" / "templates" / "cost_tracker_template.json")
        .read_text(encoding="utf-8"))

    assert tracker["approval_scope"] == init_module.DEFAULT_APPROVAL_SCOPE
    assert {
        "budget_limit", "execution_mode", "usd_cost", "cli_quota",
        "fallback_role", "route_history", "metrics_history",
    } <= tracker.keys()


def make_skill_root(tmp_path, monkeypatch, scope=None):
    (tmp_path / "scripts").mkdir()
    templates = tmp_path / "templates"
    templates.mkdir()
    (tmp_path / "tasks").mkdir()
    (templates / "task_template.md").write_text(
        "[task-name]\n[YYYY-MM-DD HH:MM]", encoding="utf-8")
    (templates / "context_template.md").write_text("", encoding="utf-8")
    (templates / "log_template.md").write_text(
        "[YYYY-MM-DD HH:MM]", encoding="utf-8")
    tracker = {
        "budget_limit": 2.0,
        "approval_scope": (
            init_module.DEFAULT_APPROVAL_SCOPE if scope is None else scope),
    }
    (templates / "cost_tracker_template.json").write_text(
        json.dumps(tracker), encoding="utf-8")
    monkeypatch.setattr(
        init_module, "__file__", str(tmp_path / "scripts" / "init_task.py"))
    return templates


def test_init_task_preserves_approval_scope(tmp_path, monkeypatch):
    make_skill_root(tmp_path, monkeypatch)

    init_module.init_task("new-task")

    tracker = json.loads(
        (tmp_path / "tasks" / "new-task" / "cost_tracker.json")
        .read_text(encoding="utf-8"))
    assert tracker["approval_scope"] == init_module.DEFAULT_APPROVAL_SCOPE


@pytest.mark.parametrize("scope", [
    {},
    {"tier": "0", "paths": [], "groups": [], "routes": {},
     "auto_merge": False, "scope_hash": ""},
])
def test_invalid_scope_fails_before_initialization(
        tmp_path, monkeypatch, capsys, scope):
    make_skill_root(tmp_path, monkeypatch, scope)

    with pytest.raises(ValueError, match="approval_scope"):
        init_module.init_task("bad-task")

    assert not (tmp_path / "tasks" / "bad-task").exists()
    assert "Successfully initialized" not in capsys.readouterr().out


def test_missing_template_fails_explicitly(tmp_path, monkeypatch):
    templates = make_skill_root(tmp_path, monkeypatch)
    (templates / "cost_tracker_template.json").unlink()

    with pytest.raises(FileNotFoundError, match="template not found"):
        init_module.init_task("missing-template")


def test_existing_task_behavior_is_preserved(tmp_path, monkeypatch):
    make_skill_root(tmp_path, monkeypatch)
    existing = tmp_path / "tasks" / "existing"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        init_module.init_task("existing")

    assert error.value.code == 1
    assert marker.read_text(encoding="utf-8") == "unchanged"
