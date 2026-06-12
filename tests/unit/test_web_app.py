"""Unit tests for the Flask web UI against a seeded run history.

These tests render every page-serving route against a temp SQLite database
holding one stored run, exercising the report and trend paths that the
existing :mod:`tests.unit.test_web` smoke tests leave on empty stores. The
:class:`RunBroker` is also covered directly, since it is process-local and
needs no socket or thread to drive.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from mfg_test_controller.controller.sequencer import StationReport, StepOutcome
from mfg_test_controller.store import RunStore
from mfg_test_controller.web import WebConfig, create_app
from mfg_test_controller.web.app import RunBroker

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_run(db_url: str, plan: str = "station_bringup") -> int:
    """Persist a small two-step run and return its id."""
    store = RunStore(db_url)
    report = StationReport(plan_name=plan)
    report.outcomes = [
        StepOutcome("read_voltage", "dmm", "read", "dc_voltage", True, 5000.0, "ok", 0.001),
        StepOutcome("read_current", "dmm", "read", "dc_current", True, 120.0, "ok", 0.002),
    ]
    report.duration_s = 0.05
    return store.save_report(report, {"dmm": "dmm"})


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'web-app.db'}"


@pytest.fixture()
def client(db_url: str) -> Iterator[FlaskClient]:
    config = WebConfig(
        db_url=db_url,
        plans_dir=REPO_ROOT / "plans",
        profiles_dir=REPO_ROOT / "profiles",
    )
    app = create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_index_shows_recent_run(client: FlaskClient, db_url: str) -> None:
    _seed_run(db_url)
    response = client.get("/")
    assert response.status_code == 200
    body = response.data.decode()
    assert "station_bringup" in body
    assert "PASS" in body


def test_run_detail_renders_steps(client: FlaskClient, db_url: str) -> None:
    run_id = _seed_run(db_url)
    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.data.decode()
    assert "station_bringup" in body
    assert "read_voltage" in body
    assert "dc_voltage" in body
    assert "PASS" in body


def test_run_detail_missing_id_returns_404(client: FlaskClient) -> None:
    response = client.get("/runs/4242")
    assert response.status_code == 404


def test_runs_list_includes_seeded_run(client: FlaskClient, db_url: str) -> None:
    run_id = _seed_run(db_url)
    response = client.get("/runs")
    assert response.status_code == 200
    body = response.data.decode()
    assert "All runs" in body
    assert f"#{run_id}" in body
    assert "station_bringup" in body


def test_trends_page_renders_measured_register(client: FlaskClient, db_url: str) -> None:
    # Several runs of the same register give the trend analysis a series.
    for value in (4990.0, 5000.0, 5010.0):
        store = RunStore(db_url)
        report = StationReport(plan_name="station_bringup")
        report.outcomes = [
            StepOutcome("read_voltage", "dmm", "read", "dc_voltage", True, value, "ok", 0.001),
        ]
        report.duration_s = 0.01
        store.save_report(report, {"dmm": "dmm"})

    response = client.get("/trends")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Measurement trends" in body
    assert "dc_voltage" in body
    assert "Markdown report" in body


def test_run_plan_post_returns_run_id_and_stream_url(client: FlaskClient) -> None:
    response = client.post("/plans/station_bringup/run")
    assert response.status_code == 200
    payload = response.get_json()
    assert "run_id" in payload
    assert payload["stream_url"].endswith(payload["run_id"])


def test_broker_open_get_and_publish_step() -> None:
    broker = RunBroker()
    run_uuid = broker.open("station_bringup")
    channel = broker.get(run_uuid)
    assert channel is not None
    assert channel.plan_name == "station_bringup"

    outcome = StepOutcome("read_voltage", "dmm", "read", "dc_voltage", True, 5000.0, "ok", 0.001)
    broker.publish_step(run_uuid, outcome, 0)
    event = channel.queue.get_nowait()
    assert event["kind"] == "step"
    assert event["ordinal"] == 0
    assert event["name"] == "read_voltage"
    assert event["passed"] is True


def test_broker_publish_done_sets_finished() -> None:
    broker = RunBroker()
    run_uuid = broker.open("demo")
    channel = broker.get(run_uuid)
    assert channel is not None

    report = StationReport(plan_name="demo")
    report.outcomes = [
        StepOutcome("step_a", "dmm", "read", "dc_voltage", True, 5000.0, "ok", 0.001),
    ]
    report.duration_s = 0.02
    broker.publish_done(run_uuid, report, run_id=7)

    assert channel.finished.is_set()
    assert channel.run_id == 7
    event = channel.queue.get_nowait()
    assert event["kind"] == "done"
    assert event["run_id"] == 7
    assert event["all_passed"] is True


def test_broker_publish_error_sets_finished() -> None:
    broker = RunBroker()
    run_uuid = broker.open("demo")
    channel = broker.get(run_uuid)
    assert channel is not None

    broker.publish_error(run_uuid, "ValueError: boom")
    assert channel.finished.is_set()
    assert channel.error == "ValueError: boom"
    event = channel.queue.get_nowait()
    assert event["kind"] == "error"
    assert event["message"] == "ValueError: boom"


def test_broker_publish_to_unknown_run_is_ignored() -> None:
    broker = RunBroker()
    outcome = StepOutcome("step_a", "dmm", "read", "dc_voltage", True, 5000.0, "ok", 0.001)
    # None of these raise even though the run id was never opened.
    broker.publish_step("missing", outcome, 0)
    broker.publish_done("missing", StationReport(plan_name="demo"), run_id=1)
    broker.publish_error("missing", "nope")
    assert broker.get("missing") is None
