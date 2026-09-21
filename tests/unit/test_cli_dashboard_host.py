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
    # config.example.yaml's `data_directory: ./data` is relative to the
    # process's cwd (the repo root, under pytest) rather than to this
    # config file, so anything here that resolves data_directory must
    # not be allowed to land on the *real* data/ directory — see
    # DEVELOPMENT.md's 2026-09-21 dated note on exactly that happening.
    text = dest.read_text()
    assert "data_directory: ./data" in text  # sanity: config.example.yaml's shape hasn't drifted
    dest.write_text(text.replace("data_directory: ./data", f"data_directory: {tmp_path / 'data'}"))
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


def test_dashboard_run_serves_plain_http_when_no_tls_configured(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    captured_kwargs: dict = {}
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: captured_kwargs.update(kwargs))

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "run"])

    assert result.exit_code == 0, result.output
    assert captured_kwargs["ssl_context"] is None
    assert "http://127.0.0.1:8765" in result.output
    assert "https://" not in result.output


def _add_tls_paths(config_path: Path, cert_path: Path, key_path: Path) -> None:
    # Appending a second top-level `dashboard:` block would silently
    # *replace* the tracked example's host/port rather than merge with
    # them (plain YAML has no notion of merging duplicate keys) — so
    # this inserts the two new lines inside the existing block instead.
    text = config_path.read_text()
    assert "port: 8765" in text  # sanity: config.example.yaml's shape hasn't drifted
    config_path.write_text(
        text.replace("port: 8765", f"port: 8765\n  tls_cert_path: {cert_path}\n  tls_key_path: {key_path}")
    )


def test_dashboard_run_serves_https_when_tls_cert_and_key_exist(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    cert_path = config_path.parent / "dashboard.crt"
    key_path = config_path.parent / "dashboard.key"
    cert_path.write_text("fake cert")  # never parsed — Flask.run itself is monkeypatched below
    key_path.write_text("fake key")
    _add_tls_paths(config_path, cert_path, key_path)

    captured_kwargs: dict = {}
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: captured_kwargs.update(kwargs))

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "run"])

    assert result.exit_code == 0, result.output
    assert captured_kwargs["ssl_context"] == (str(cert_path), str(key_path))
    assert "https://127.0.0.1:8765" in result.output


def test_dashboard_run_falls_back_to_http_when_tls_files_missing(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    missing_cert = config_path.parent / "does-not-exist.crt"
    missing_key = config_path.parent / "does-not-exist.key"
    _add_tls_paths(config_path, missing_cert, missing_key)

    captured_kwargs: dict = {}
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: captured_kwargs.update(kwargs))

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "run"])

    assert result.exit_code == 0, result.output
    assert captured_kwargs["ssl_context"] is None
    assert "http://127.0.0.1:8765" in result.output


def test_dashboard_url_reflects_https_when_tls_configured(config_path: Path) -> None:
    cert_path = config_path.parent / "dashboard.crt"
    key_path = config_path.parent / "dashboard.key"
    _add_tls_paths(config_path, cert_path, key_path)

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "url"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "https://127.0.0.1:8765"
