"""Backlot reads OpenMontage Studio's ``studio_run.json`` sidecar (OM-1)."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backlot import state as state_mod
from backlot.state import load_board_state, read_studio_run, summarize_project


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    p = root / "ca-1"
    (p / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", root)
    (p / "project.json").write_text(json.dumps(
        {"project_id": "ca-1", "title": "T", "pipeline_type": "character-animation"}),
        encoding="utf-8")
    return p


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _run(p: Path, **kw) -> None:
    now = time.time()
    data = {
        "version": 1, "source": "studio", "project_id": p.name, "run_id": "r1",
        "run_number": 1, "status": "running", "agent_id": "claude",
        "agent_label": "Claude CLI", "model": "opus", "live": True,
        "started_at": _iso(now - 120), "last_event_at": _iso(now - 5),
        "last_event": "→ Bash · Inspect project.json",
        "studio_url": "http://127.0.0.1:8790/projects/ca-1", "updated_at": _iso(now - 5),
    }
    data.update(kw)
    (p / "studio_run.json").write_text(json.dumps(data), encoding="utf-8")


def test_no_sidecar_keeps_old_behaviour(project):
    s = load_board_state(project)
    assert s["studio_run"] is None
    assert s["live"] is False
    assert summarize_project(project)["studio_status"] is None


def test_running_without_checkpoints_is_live(project):
    _run(project)
    s = load_board_state(project)
    assert s["has_pipeline_state"] is False
    assert s["live"] is True
    run = s["studio_run"]
    assert run["status"] == "running" and run["run_number"] == 1
    assert run["last_event"].startswith("→ Bash")
    assert run["started_at_epoch"] and "stalled_minutes" not in run
    assert s["last_activity"] >= time.time() - 10
    assert summarize_project(project)["studio_status"] == "running"


def test_quiet_running_agent_is_flagged(project):
    old = time.time() - 15 * 60
    _run(project, started_at=_iso(old - 60), last_event_at=_iso(old), updated_at=_iso(old))
    run = load_board_state(project)["studio_run"]
    assert run["stalled_minutes"] >= 14


@pytest.mark.parametrize("status", ["queued", "waiting_approval", "failed", "succeeded", "cancelled"])
def test_statuses_pass_through(project, status):
    _run(project, status=status, queue_position=2, pending_gate="proposal", error="boom")
    run = load_board_state(project)["studio_run"]
    assert run["status"] == status
    assert run["queue_position"] == 2 and run["pending_gate"] == "proposal"


def test_invalid_sidecar_is_ignored(project):
    (project / "studio_run.json").write_text("{not json", encoding="utf-8")
    assert read_studio_run(project) is None
    _run(project, status="exploded")
    assert read_studio_run(project) is None


def test_sanitises_fields(project):
    _run(project, studio_url="javascript:alert(1)", last_event="x" * 1000,
         run_number=True, queue_position=-3, secret="sk-123", live="yes")
    run = read_studio_run(project)
    assert run["studio_url"] is None
    assert len(run["last_event"]) == 200
    assert run["run_number"] is None and run["queue_position"] is None
    assert "secret" not in run and run["live"] is False


def test_future_timestamp_does_not_pin_activity(project):
    _run(project, status="succeeded", updated_at=_iso(time.time() + 86400),
         last_event_at=None, started_at=None)
    s = load_board_state(project)
    assert s["last_activity"] < time.time() + 120


def test_queued_run_is_not_live(project):
    _run(project, status="queued", queue_position=1, last_event=None,
         last_event_at=None, started_at=None)
    s = load_board_state(project)
    assert s["live"] is False
    assert summarize_project(project)["studio_status"] == "queued"
