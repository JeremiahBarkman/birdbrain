"""Boot-time auto-start: systemd unit files (Linux) / launchd
LaunchAgent plists (macOS) for the four long-running services
(capture, analyzer, images-watch, dashboard). Completes requirements
§29 Phase 8 - until now, both platforms only had the manual
scripts/start_all.sh/stop_all.sh launcher, with nothing surviving a
reboot without someone running it by hand.

Per requirements §31.1 and this project's own README (Microphone
section): macOS blocks microphone capture entirely for any process
with no attached GUI/WindowServer session. This means the services
here MUST ship as a launchd **LaunchAgent**
(~/Library/LaunchAgents/, tied to a logged-in GUI session) - never a
**LaunchDaemon** (/Library/LaunchDaemons/, system-level, no GUI
session), which would hit that exact wall permanently and silently
(CoreAudio hands back silence, not an error - see the README's
Microphone section for how this was actually discovered). Linux has no
equivalent restriction (confirmed live on the Raspberry Pi - ALSA
needs no GUI session), so its systemd units are ordinary system-level
services running as the installing user.

Rendering (pure functions, fully unit-testable without root or a real
service manager) is kept separate from actually writing files and
calling systemctl/launchctl - see cli.py's `services` command group.
"""
from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServiceDefinition:
    name: str  # short id -> unit "birdbrain-{name}.service" / label "com.backyardbird.{name}"
    description: str
    cli_args: tuple[str, str]


SERVICE_DEFINITIONS: tuple[ServiceDefinition, ...] = (
    ServiceDefinition("capture", "continuous audio capture (§8)", ("capture", "run")),
    ServiceDefinition("analyzer", "BirdNET analysis worker (§10)", ("analyze", "run")),
    ServiceDefinition(
        "images-watch", "automatic image search for newly detected species (§15.1)", ("images", "watch")
    ),
    ServiceDefinition("dashboard", "local status dashboard (§22)", ("dashboard", "run")),
)


def systemd_unit_filename(service: ServiceDefinition) -> str:
    return f"birdbrain-{service.name}.service"


def launchd_label(service: ServiceDefinition) -> str:
    return f"com.backyardbird.{service.name}"


def launchd_plist_filename(service: ServiceDefinition) -> str:
    return f"{launchd_label(service)}.plist"


def render_systemd_unit(service: ServiceDefinition, *, repo_dir: Path, venv_bin: Path, user: str) -> str:
    """A system-level unit (not --user): avoids needing `loginctl
    enable-linger` for the service to start before any login on a
    headless boot, at the cost of needing sudo to install (§20.1-style
    independence is still per-service - one crashing unit doesn't stop
    the others, since each is its own unit with its own Restart=).
    """
    bird_display = venv_bin / "bird-display"
    args = " ".join(service.cli_args)
    return (
        "[Unit]\n"
        f"Description=Backyard Bird Discovery System - {service.description}\n"
        "After=network.target sound.target\n"
        "StartLimitIntervalSec=0\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={repo_dir}\n"
        f"ExecStart={bird_display} {args}\n"
        "Restart=on-failure\n"
        "RestartSec=10\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_launchd_plist(service: ServiceDefinition, *, repo_dir: Path, venv_bin: Path) -> bytes:
    """plistlib (stdlib), not hand-built XML - correct escaping for
    any repo path is not something worth hand-rolling.
    """
    bird_display = str(venv_bin / "bird-display")
    log_dir = repo_dir / "data" / "logs"
    plist = {
        "Label": launchd_label(service),
        "ProgramArguments": [bird_display, *service.cli_args],
        "WorkingDirectory": str(repo_dir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / f"{service.name}.launchd.out.log"),
        "StandardErrorPath": str(log_dir / f"{service.name}.launchd.err.log"),
    }
    return plistlib.dumps(plist)
