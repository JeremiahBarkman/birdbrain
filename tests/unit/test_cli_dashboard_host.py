import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from flask import Flask

from backyard_bird.cli import _resolve_dashboard_host_port, cli
from backyard_bird.config import DashboardConfig


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    # Copying the tracked example config (rather than hand-writing a
    # minimal one here) keeps this test from drifting out of sync with
    # config.yaml's required fields.
    dest = tmp_path / "config.yaml"
    shutil.copy(Path("config/config.example.yaml"), dest)
    return dest


def test_dashboard_run_passes_reload_flag_to_flask(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    captured_kwargs: dict = {}
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: captured_kwargs.update(kwargs))

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "run", "--reload"])

    assert result.exit_code == 0, result.output
    assert captured_kwargs["use_reloader"] is True
    assert captured_kwargs["debug"] is False  # the reloader, not the interactive debugger


def test_dashboard_run_defaults_reload_off(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    captured_kwargs: dict = {}
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: captured_kwargs.update(kwargs))

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "run"])

    assert result.exit_code == 0, result.output
    assert captured_kwargs["use_reloader"] is False


def test_config_values_used_when_no_cli_flags_given() -> None:
    config = DashboardConfig(host="0.0.0.0", port=9000)
    host, port = _resolve_dashboard_host_port(None, None, config)
    assert (host, port) == ("0.0.0.0", 9000)


def test_cli_flags_override_config() -> None:
    config = DashboardConfig(host="0.0.0.0", port=9000)
    host, port = _resolve_dashboard_host_port("192.168.1.50", 8080, config)
    assert (host, port) == ("192.168.1.50", 8080)


def test_defaults_are_localhost_only() -> None:
    config = DashboardConfig()
    host, port = _resolve_dashboard_host_port(None, None, config)
    assert (host, port) == ("127.0.0.1", 8765)
