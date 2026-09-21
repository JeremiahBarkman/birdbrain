import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from backyard_bird.cli import _all_local_ipv4_addresses, cli


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    dest = tmp_path / "config.yaml"
    shutil.copy(Path("config/config.example.yaml"), dest)
    # config.example.yaml's `data_directory: ./data` is relative to the
    # process's cwd, not to this config file — under pytest that's the
    # repo root, so left unpatched this resolves to the *real*
    # data/ directory. generate-cert (and anything else that resolves
    # data_directory) would then write real cert/key files, which is
    # exactly what clobbered the live deployment's cert once already
    # (see DEVELOPMENT.md's 2026-09-21 dated note). Every test in this
    # file must use an isolated, absolute data_directory instead.
    text = dest.read_text()
    assert "data_directory: ./data" in text  # sanity: config.example.yaml's shape hasn't drifted
    dest.write_text(text.replace("data_directory: ./data", f"data_directory: {tmp_path / 'data'}"))
    return dest


def test_all_local_ipv4_addresses_parses_hostname_dash_i(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="10.0.0.202 10.0.0.197 \n", stderr=""),
    )

    assert _all_local_ipv4_addresses() == ["10.0.0.202", "10.0.0.197"]


def test_all_local_ipv4_addresses_skips_ipv6_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="10.0.0.202 fe80::1234:5678\n", stderr=""),
    )

    assert _all_local_ipv4_addresses() == ["10.0.0.202"]


def test_all_local_ipv4_addresses_falls_back_when_hostname_command_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")

    def _raise(*a, **k):
        raise FileNotFoundError("no hostname command")

    monkeypatch.setattr("subprocess.run", _raise)
    monkeypatch.setattr("backyard_bird.cli._lan_ip", lambda: "192.168.1.50")

    assert _all_local_ipv4_addresses() == ["192.168.1.50"]


def test_all_local_ipv4_addresses_uses_fallback_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("backyard_bird.cli._lan_ip", lambda: "192.168.1.50")

    assert _all_local_ipv4_addresses() == ["192.168.1.50"]


def test_generate_cert_fails_cleanly_when_openssl_is_missing(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "generate-cert"])

    assert result.exit_code != 0
    assert "openssl" in result.output.lower()


def test_generate_cert_fails_cleanly_when_no_lan_address_found(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/openssl")
    monkeypatch.setattr("backyard_bird.cli._all_local_ipv4_addresses", lambda: [])

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "generate-cert"])

    assert result.exit_code != 0
    assert "LAN IP" in result.output


def test_generate_cert_invokes_openssl_and_prints_config_lines(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, tmp_path: Path
) -> None:
    captured_args: list = []

    def _fake_run(args, **kwargs):
        captured_args.append(args)
        # The real command would create these files — simulate that so
        # the command's own success-path file references stay valid.
        key_path = Path(args[args.index("-keyout") + 1])
        cert_path = Path(args[args.index("-out") + 1])
        key_path.write_text("fake key")
        cert_path.write_text("fake cert")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/openssl")
    monkeypatch.setattr("backyard_bird.cli._all_local_ipv4_addresses", lambda: ["10.0.0.202", "10.0.0.197"])
    monkeypatch.setattr("subprocess.run", _fake_run)

    result = CliRunner().invoke(cli, ["--config", str(config_path), "dashboard", "generate-cert"])

    assert result.exit_code == 0, result.output
    assert len(captured_args) == 1
    san_arg = captured_args[0][captured_args[0].index("-addext") + 1]
    assert "IP:10.0.0.202" in san_arg
    assert "IP:10.0.0.197" in san_arg
    assert "IP:127.0.0.1" in san_arg
    assert "DNS:localhost" in san_arg
    assert "tls_cert_path:" in result.output
    assert "tls_key_path:" in result.output
