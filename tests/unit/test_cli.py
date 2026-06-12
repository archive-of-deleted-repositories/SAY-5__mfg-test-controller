"""Unit tests for the Click command-line interface.

Each command is driven through Click's :class:`CliRunner` against the
in-process simulated devices and a throwaway SQLite database under
``tmp_path``. The built-in device profiles cover every plan device, so the
``run`` and ``replay`` commands stay hermetic without touching the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mfg_test_controller.cli import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN = REPO_ROOT / "plans" / "station_bringup.yaml"
DMM_PROFILE = REPO_ROOT / "profiles" / "dmm.yaml"


def _db_url(tmp_path: Path, name: str = "cli-test.db") -> str:
    return f"sqlite:///{tmp_path / name}"


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_cli_help_lists_all_commands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("devices", "run", "report", "replay", "simulate-fault", "trends", "serve"):
        assert command in result.output
    assert "serve-web" in result.output


def test_devices_lists_builtin_profiles(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["devices"])
    assert result.exit_code == 0
    assert "power_supply" in result.output
    assert "dmm" in result.output
    assert "actuator" in result.output
    assert "thermocouple" in result.output
    assert "unit_id=" in result.output


def test_run_executes_plan_and_saves_report(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["run", str(PLAN), "--db", _db_url(tmp_path)])
    assert result.exit_code == 0
    assert "[PASS]" in result.output
    assert "11/11 passed" in result.output
    assert "saved as run #1" in result.output


def test_run_writes_json_and_markdown_reports(runner: CliRunner, tmp_path: Path) -> None:
    json_out = tmp_path / "report.json"
    md_out = tmp_path / "report.md"
    result = runner.invoke(
        cli,
        [
            "run",
            str(PLAN),
            "--db",
            _db_url(tmp_path),
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(json_out.read_text())
    assert payload["summary"]["total"] == 11
    assert payload["summary"]["all_passed"] is True
    assert "Result: PASS" in md_out.read_text()


def test_run_modbus_tcp_framing(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        cli, ["run", str(PLAN), "--db", _db_url(tmp_path), "--framing", "modbus-tcp"]
    )
    assert result.exit_code == 0
    assert "11/11 passed" in result.output


def test_run_only_failed_without_prior_run_errors(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["run", str(PLAN), "--db", _db_url(tmp_path), "--only-failed"])
    assert result.exit_code != 0
    assert "no prior run" in result.output


def test_run_only_failed_after_passing_run_is_noop(runner: CliRunner, tmp_path: Path) -> None:
    db = _db_url(tmp_path)
    first = runner.invoke(cli, ["run", str(PLAN), "--db", db])
    assert first.exit_code == 0
    second = runner.invoke(cli, ["run", str(PLAN), "--db", db, "--only-failed"])
    assert second.exit_code == 0
    assert "nothing to do" in second.output


def test_report_lists_runs_then_shows_one(runner: CliRunner, tmp_path: Path) -> None:
    db = _db_url(tmp_path)
    runner.invoke(cli, ["run", str(PLAN), "--db", db])

    listing = runner.invoke(cli, ["report", "--db", db])
    assert listing.exit_code == 0
    assert "station_bringup" in listing.output
    assert "PASS" in listing.output

    detail = runner.invoke(cli, ["report", "1", "--db", db])
    assert detail.exit_code == 0
    assert "Run #1: station_bringup" in detail.output
    assert "11/11 passed" in detail.output


def test_report_json_format(runner: CliRunner, tmp_path: Path) -> None:
    db = _db_url(tmp_path)
    runner.invoke(cli, ["run", str(PLAN), "--db", db])
    result = runner.invoke(cli, ["report", "1", "--db", db, "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["id"] == 1
    assert payload["plan"] == "station_bringup"
    assert payload["total"] == 11


def test_report_empty_store(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["report", "--db", _db_url(tmp_path)])
    assert result.exit_code == 0
    assert "no runs recorded yet" in result.output


def test_report_unknown_run_id_errors(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["report", "999", "--db", _db_url(tmp_path)])
    assert result.exit_code != 0
    assert "no run with id 999" in result.output


def test_replay_reruns_and_reports_delta(runner: CliRunner, tmp_path: Path) -> None:
    db = _db_url(tmp_path)
    runner.invoke(cli, ["run", str(PLAN), "--db", db])
    result = runner.invoke(cli, ["replay", str(PLAN), "--db", db])
    assert result.exit_code == 0
    assert "[PASS]" in result.output
    assert "previous run #1" in result.output
    assert "change in passing steps: +0" in result.output


def test_replay_without_prior_history(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["replay", str(PLAN), "--db", _db_url(tmp_path)])
    assert result.exit_code == 0
    assert "[PASS]" in result.output
    assert "previous run" not in result.output


def test_simulate_fault_prints_device_state(runner: CliRunner) -> None:
    result = runner.invoke(
        cli,
        [
            "simulate-fault",
            "--profile",
            str(DMM_PROFILE),
            "--fault",
            "drift",
            "--register",
            "0",
            "--amount",
            "10",
        ],
    )
    assert result.exit_code == 0
    assert "profile: dmm  fault: drift" in result.output
    assert "dc_voltage" in result.output
    assert "baseline=4980" in result.output
    assert "fault configured" in result.output


def test_simulate_fault_rejects_unknown_kind(runner: CliRunner) -> None:
    result = runner.invoke(
        cli,
        ["simulate-fault", "--profile", str(DMM_PROFILE), "--fault", "explode"],
    )
    assert result.exit_code != 0


def test_trends_reports_no_history_when_empty(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["trends", "--db", _db_url(tmp_path)])
    assert result.exit_code == 0
    assert "no measurement history found" in result.output


def test_trends_summarises_stored_runs(runner: CliRunner, tmp_path: Path) -> None:
    db = _db_url(tmp_path)
    for _ in range(3):
        runner.invoke(cli, ["run", str(PLAN), "--db", db])
    result = runner.invoke(cli, ["trends", "--db", db])
    assert result.exit_code == 0
    assert "dc_voltage" in result.output


def test_trends_export_to_stdout(runner: CliRunner, tmp_path: Path) -> None:
    db = _db_url(tmp_path)
    for _ in range(3):
        runner.invoke(cli, ["run", str(PLAN), "--db", db])
    result = runner.invoke(cli, ["trends", "--db", db, "--export"])
    assert result.exit_code == 0
    assert "# Measurement trend analysis" in result.output


def test_trends_export_to_file(runner: CliRunner, tmp_path: Path) -> None:
    db = _db_url(tmp_path)
    for _ in range(3):
        runner.invoke(cli, ["run", str(PLAN), "--db", db])
    out = tmp_path / "trends.md"
    result = runner.invoke(cli, ["trends", "--db", db, "--export", str(out)])
    assert result.exit_code == 0
    assert "trend report:" in result.output
    assert "# Measurement trend analysis" in out.read_text()


def test_trends_filter_by_register(runner: CliRunner, tmp_path: Path) -> None:
    db = _db_url(tmp_path)
    for _ in range(3):
        runner.invoke(cli, ["run", str(PLAN), "--db", db])
    result = runner.invoke(cli, ["trends", "--db", db, "--register", "dc_voltage"])
    assert result.exit_code == 0
    assert "dc_voltage" in result.output


def test_serve_help_wires_up_options(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.output
    assert "--port" in result.output
    assert "--framing" in result.output


def test_serve_web_help_wires_up_options(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["serve-web", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--plans-dir" in result.output
    assert "--profiles-dir" in result.output
