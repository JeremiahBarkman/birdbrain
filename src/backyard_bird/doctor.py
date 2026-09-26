"""Environment/health checks backing `bird-display doctor` (§25).

Each check is a plain function returning a CheckResult so the CLI can
just format and print them — no business logic in cli.py or in
scripts/install.sh (CLAUDE.md rule: don't put business logic in shell
scripts). install.sh runs `bird-display doctor` as its final
prerequisite gate after setting up the venv.

Every §25 doctor item is now covered: platform, Python, BirdNET,
directories, disk, audio devices, microphone permission, service
auto-start, database access, image-provider configuration, frame
configuration, and network access. The last four were added
2026-09-26 — they had been deferred as "features not yet built", which
stopped being true once images (§29 Phase 4) and the frame adapter
package (Phase 6) landed, and database access was simply never
written despite being on §25's list from the start.

Only checks that gate *audio capture* may fail. Image, frame and
network problems warn: capture must never depend on internet
connectivity (CLAUDE.md rule 11) and image/frame failures must never
stop detection (rule 12), so `doctor` exiting non-zero over an
unreachable Wikimedia would misreport a system that is in fact
recording birds correctly.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["pass", "warn", "fail"]

# Mirrors pyproject.toml's requires-python / the tensorflow<2.17 pin
# that forces it. Keep these in sync.
MIN_PYTHON = (3, 9)
MAX_PYTHON_EXCLUSIVE = (3, 12)

MINIMUM_FREE_DISK_GB = 5.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    message: str


def _capture_process_is_running() -> bool:
    """Best-effort check via `pgrep` (bundled on both target OSes, no
    new dependency). Used only to improve the accuracy of
    check_audio_devices/check_microphone_permission's messages when
    devices come back empty - never trusted as a sole signal of
    anything else. If pgrep itself is missing or errors, this
    conservatively reports False (not running), which just falls back
    to this project's original, stricter FAIL behavior rather than
    silently hiding a real problem.
    """
    import subprocess

    try:
        result = subprocess.run(["pgrep", "-f", "bird-display capture run"], capture_output=True, text=True)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def check_platform() -> CheckResult:
    """This project targets two host profiles (requirements §7.1): a
    macOS Mac mini and a Linux (Ubuntu) host such as a Raspberry Pi —
    catch anything else immediately rather than let someone burn ten
    minutes on a doomed tensorflow/tflite-runtime install.
    """
    import platform

    system = platform.system()
    machine = platform.machine()

    if system == "Darwin":
        if machine == "x86_64":
            return CheckResult(
                "platform",
                "warn",
                "Intel Mac detected. Every pinned dependency (including tensorflow) "
                "publishes an x86_64 wheel, so `pip install` is expected to succeed — "
                "but this project is only developed and tested on Apple Silicon "
                "hardware, so runtime behavior (BirdNET performance, audio device "
                "handling) hasn't been verified there. Report issues if you hit any.",
            )
        return CheckResult("platform", "pass", f"macOS, {machine}")

    if system == "Linux":
        if machine not in ("aarch64", "x86_64"):
            return CheckResult(
                "platform",
                "warn",
                f"Linux on {machine} detected. This project is developed and tested "
                "against Ubuntu 24.04 LTS on aarch64 (Raspberry Pi 4B) — tflite-runtime "
                f"may not publish a wheel for {machine}. Report issues if you hit any.",
            )
        return CheckResult("platform", "pass", f"Linux, {machine}")

    return CheckResult(
        "platform",
        "fail",
        f"Detected {system}, but this project targets macOS or Linux (Ubuntu). Audio "
        "capture and the installer assume one of those.",
    )


def check_python_version() -> CheckResult:
    version = sys.version_info
    if (version.major, version.minor) < MIN_PYTHON or (version.major, version.minor) >= MAX_PYTHON_EXCLUSIVE:
        return CheckResult(
            "python_version",
            "fail",
            f"Running Python {version.major}.{version.minor} — this project needs "
            f">={MIN_PYTHON[0]}.{MIN_PYTHON[1]},<{MAX_PYTHON_EXCLUSIVE[0]}.{MAX_PYTHON_EXCLUSIVE[1]} "
            "(tensorflow's supported range). Recreate the virtual environment with a "
            "compatible interpreter — see scripts/install.sh.",
        )
    return CheckResult(
        "python_version", "pass", f"Python {version.major}.{version.minor}.{version.micro}"
    )


def check_birdnet(sample_wav: Path | None = None) -> CheckResult:
    """The real "is BirdNET installed" check: BirdNET-Analyzer's model
    ships inside the birdnetlib wheel itself (no separate install
    step), so the only meaningful test is loading it and, if a sample
    WAV is available, actually analyzing something end to end — the
    Phase 1 exit condition (§29).
    """
    try:
        from birdnetlib.analyzer import Analyzer
    except Exception as exc:  # pragma: no cover - exercised via mocking in tests
        return CheckResult(
            "birdnet",
            "fail",
            f"Could not import birdnetlib: {exc}. Run scripts/install.sh (or "
            "`pip install -e .`) to install dependencies.",
        )

    try:
        analyzer = Analyzer()
    except Exception as exc:
        return CheckResult(
            "birdnet",
            "fail",
            f"BirdNET model failed to load: {exc}. Common causes: an incompatible "
            "tensorflow wheel for this machine's architecture, or a corrupted "
            "install — try `pip install --force-reinstall birdnetlib tensorflow`.",
        )

    if sample_wav is None or not sample_wav.exists():
        return CheckResult(
            "birdnet",
            "warn",
            "BirdNET model loaded, but no sample WAV was available to fully "
            "exercise analysis. See tests/sample_audio/README.md.",
        )

    from backyard_bird.analysis.birdnet_adapter import analyze_file
    from backyard_bird.config import BirdNETConfig

    try:
        result = analyze_file(sample_wav, BirdNETConfig())
    except Exception as exc:
        return CheckResult("birdnet", "fail", f"BirdNET analysis of {sample_wav} failed: {exc}")

    return CheckResult(
        "birdnet",
        "pass",
        f"BirdNET v{result.birdnet_version} analyzed {sample_wav.name} in "
        f"{result.analysis_duration_seconds:.1f}s ({len(result.detections)} detection(s)).",
    )


def check_required_directories(data_directory: Path) -> CheckResult:
    """The list comes from layout.py, which is also what the writers
    resolve their paths through — this check once created audio/clips
    while every writer used audio/best_clips, and nothing noticed
    because mkdir(exist_ok=True) can't fail on a name nobody reads."""
    from backyard_bird.layout import DataLayout

    required = DataLayout.under(data_directory).required_directories()
    failed = []
    for path in required:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            failed.append(str(path.relative_to(data_directory)))

    if failed:
        return CheckResult(
            "required_directories",
            "fail",
            f"Could not create: {', '.join(failed)} under {data_directory}",
        )
    return CheckResult(
        "required_directories", "pass", f"All {len(required)} data subdirectories exist under {data_directory}"
    )


def check_disk_space(data_directory: Path) -> CheckResult:
    check_path = data_directory if data_directory.exists() else data_directory.parent
    try:
        usage = shutil.disk_usage(check_path)
    except OSError as exc:
        return CheckResult("disk_space", "warn", f"Could not check disk space: {exc}")

    free_gb = usage.free / (1024**3)
    if free_gb < MINIMUM_FREE_DISK_GB:
        return CheckResult(
            "disk_space",
            "warn",
            f"Only {free_gb:.1f} GB free. Continuous audio capture and BirdNET's "
            f"detection clips accumulate over time — {MINIMUM_FREE_DISK_GB:.0f}+ GB free is recommended.",
        )
    return CheckResult("disk_space", "pass", f"{free_gb:.1f} GB free")


def check_audio_devices(configured_device_name: str | None = None) -> CheckResult:
    from backyard_bird.audio.devices import find_input_device, list_input_devices

    try:
        devices = list_input_devices()
    except Exception as exc:
        return CheckResult("audio_devices", "fail", f"Could not query audio devices: {exc}")

    if not devices:
        import platform

        if platform.system() == "Linux" and _capture_process_is_running():
            # Not a failure - the expected, permanent steady state once
            # auto-start is enabled (§29 Phase 8): capture_run holds the
            # device open continuously, and ALSA's raw hw:N,M nodes only
            # allow one process at a time to even query them (confirmed
            # live on the Pi). Without this branch, `doctor` would FAIL
            # this check forever in the normal healthy case - a real
            # trust problem for anyone checking its exit code after a
            # reboot, found by actually enabling auto-start and running
            # doctor right after (2026-09-14).
            return CheckResult(
                "audio_devices",
                "warn",
                "No input devices visible, but `bird-display capture run` is already active — "
                "expected: ALSA's raw hw:N,M device nodes only allow one process at a time to "
                "even query them, so this check can't see a device the running capture service "
                "already holds. Not necessarily a problem — check `bird-display queue status` "
                "or the dashboard for real segments/detections instead.",
            )

        if platform.system() == "Linux":
            hint = (
                "Check that a microphone is connected, that ALSA/PulseAudio can see it "
                "(`arecord -l`), and that this user is in the `audio` group. Also confirm "
                "`bird-display capture run` isn't already running: ALSA's raw `hw:N,M` "
                "device nodes only allow one process at a time, even just to query them "
                "(confirmed live: a running `capture run` alone makes this exact check "
                "report zero devices) — stop it first (`./scripts/stop_all.sh`) if so."
            )
        else:
            hint = "Check macOS microphone permissions for your terminal/Python and that a microphone is connected."
        return CheckResult("audio_devices", "fail", f"No input (microphone) devices found. {hint}")

    names = [d.name for d in devices]
    # find_input_device (not a plain membership check) so this agrees with what
    # capture_service.py would actually resolve - including its ALSA
    # "(hw:N,M)" fallback match, so doctor doesn't warn about a device
    # capture would in fact find fine.
    if configured_device_name and find_input_device(configured_device_name) is None:
        return CheckResult(
            "audio_devices",
            "warn",
            f"Configured audio.device_name {configured_device_name!r} not found. "
            f"Available devices: {', '.join(names)}. Run `bird-display audio list-devices`.",
        )
    return CheckResult("audio_devices", "pass", f"{len(devices)} input device(s) found: {', '.join(names)}")


def check_microphone_permission(configured_device_name: str | None = None) -> CheckResult:
    """Distinct from check_audio_devices: listing devices just queries
    the device table and needs no permission, but actually opening a
    stream can (macOS's per-app microphone TCC grant; on Linux, ALSA
    device-file permissions/the `audio` group) — the §25 "microphone
    permissions" item is separate from "audio-device availability" for
    exactly this reason. Attempts the same short real capture
    `bird-display audio test` does, so a denied permission shows up
    here instead of when capture starts.

    Linux has no TCC-style GUI-session requirement (§31.1) — this
    check can run the same over SSH as at a physical console, unlike
    on macOS.
    """
    from backyard_bird.audio.devices import find_input_device, list_input_devices

    devices = list_input_devices()
    if not devices:
        return CheckResult(
            "microphone_permission", "warn", "Skipped: no input devices found (see audio_devices)."
        )

    target = None
    if configured_device_name:
        target = find_input_device(configured_device_name)
    device = target.index if target else None
    sample_rate = int(target.default_samplerate) if target else int(devices[0].default_samplerate)

    import numpy as np
    import sounddevice as sd

    import platform

    try:
        frames = sd.rec(int(0.3 * sample_rate), samplerate=sample_rate, channels=1, dtype="int16", device=device)
        sd.wait()
    except Exception as exc:
        if platform.system() == "Linux":
            hint = (
                "On Linux: confirm this user is in the `audio` group (`groups`; "
                "`sudo usermod -aG audio $USER` then log back in if not), and that "
                "no other process (PulseAudio/PipeWire exclusive mode, another "
                "`capture run`) already holds the device open."
            )
        else:
            hint = (
                "On macOS: System Settings > Privacy & Security > Microphone, and grant "
                "access to whatever runs this (Terminal/iTerm) — if it isn't listed there "
                "yet, run `bird-display audio test` once to trigger the permission prompt."
            )
        return CheckResult("microphone_permission", "fail", f"Could not open the microphone: {exc}. {hint}")

    peak = int(np.abs(frames).max()) if frames.size else 0
    if peak == 0:
        silent_denial_note = (
            "or macOS silently denying microphone access without raising an error "
            "(it does this on some versions)"
            if platform.system() != "Linux"
            # No known Linux equivalent of macOS's silent TCC denial — ALSA/PortAudio
            # errors out instead of handing back silence for a permission problem.
            else "or a genuinely misconfigured input source (check `alsamixer`)"
        )
        return CheckResult(
            "microphone_permission",
            "warn",
            "Captured 0.3s of complete silence. Most likely `bird-display capture run` "
            "is already using this device (some USB mics hand a second, concurrent "
            "listener silence instead of an error rather than truly sharing the "
            f"stream) — harmless if so. Otherwise: a genuinely quiet room, {silent_denial_note}. "
            "Run `bird-display audio test` on its own, with capture stopped, while "
            "making noise near the mic to tell which.",
        )
    return CheckResult("microphone_permission", "pass", "Captured real audio from the microphone.")


def check_service_autostart() -> CheckResult:
    """§25: "launchd service definitions" (extended to systemd on
    Linux — see requirements §5.1/§30 rule 27). Warns, never fails:
    auto-start is a convenience on top of a fully supported manual
    alternative (scripts/start_all.sh/stop_all.sh), not a hard
    requirement for the system to function (§29 Phase 8 was the only
    thing gating this until `bird-display services install` existed).
    """
    import platform

    from backyard_bird.service_install import SERVICE_DEFINITIONS, launchd_plist_filename, systemd_unit_filename

    system = platform.system()
    if system == "Linux":
        installed = [s for s in SERVICE_DEFINITIONS if Path("/etc/systemd/system", systemd_unit_filename(s)).exists()]
    elif system == "Darwin":
        agents_dir = Path.home() / "Library" / "LaunchAgents"
        installed = [s for s in SERVICE_DEFINITIONS if (agents_dir / launchd_plist_filename(s)).exists()]
    else:
        return CheckResult("service_autostart", "warn", f"Auto-start isn't supported on {system}.")

    if not installed:
        return CheckResult(
            "service_autostart",
            "warn",
            "No auto-start services installed — after a reboot, services must be started "
            "manually (scripts/start_all.sh). Run `bird-display services install` to fix this.",
        )
    if len(installed) < len(SERVICE_DEFINITIONS):
        missing = ", ".join(s.name for s in SERVICE_DEFINITIONS if s not in installed)
        return CheckResult(
            "service_autostart",
            "warn",
            f"Only {len(installed)}/{len(SERVICE_DEFINITIONS)} services have auto-start "
            f"installed (missing: {missing}). Run `bird-display services install`.",
        )
    return CheckResult(
        "service_autostart", "pass", f"All {len(SERVICE_DEFINITIONS)} services installed for auto-start."
    )


def check_database_access(data_directory: Path, migrations_dir: Path | None = None) -> CheckResult:
    """§25 "database access". Beyond opening the file, this reports
    *pending migrations*, which is the failure mode that actually bit
    this project: `dashboard run` never applies migrations, so a
    schema-only change that ships without a manual `bird-display
    database migrate` does nothing until the first route needing the
    new table 500s (found live, 2026-09-21, migration 005).
    """
    from backyard_bird.layout import DataLayout

    db_path = DataLayout.under(data_directory).database_path
    if not db_path.exists():
        return CheckResult(
            "database_access",
            "warn",
            f"No database at {db_path} yet — it's created on first use. "
            "Run `bird-display database migrate` to create it now.",
        )

    from backyard_bird.database.connection import get_connection

    try:
        conn = get_connection(db_path)
    except Exception as exc:
        return CheckResult("database_access", "fail", f"Could not open {db_path}: {exc}")

    try:
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        except Exception as exc:
            return CheckResult("database_access", "fail", f"Could not read {db_path}: {exc}")

        if integrity != "ok":
            return CheckResult(
                "database_access",
                "fail",
                f"PRAGMA integrity_check on {db_path} returned {integrity!r}. "
                "Restore from a backup — see `bird-display database integrity-check`.",
            )

        if migrations_dir is None or not migrations_dir.is_dir():
            return CheckResult(
                "database_access", "pass", f"{db_path} opens and passes integrity_check."
            )

        from backyard_bird.database.migrations import pending_migrations

        pending = pending_migrations(conn, migrations_dir)
    finally:
        conn.close()

    if pending:
        versions = ", ".join(str(version) for version, _, _ in pending)
        return CheckResult(
            "database_access",
            "warn",
            f"{len(pending)} migration(s) not applied ({versions}). Features needing the "
            "new schema will fail at runtime until you run `bird-display database migrate`.",
        )
    return CheckResult(
        "database_access", "pass", f"{db_path} opens, passes integrity_check, schema up to date."
    )


def check_image_providers(preferred_sources: list[str] | None) -> CheckResult:
    """§25 "image-provider configuration". Neither provider needs an
    API key (§15.3 picked them partly for that), so there is no
    credential to verify — what can actually be wrong is a name in
    images.preferred_sources that this codebase doesn't implement,
    which build_providers() silently drops on its way to the Wikimedia
    fallback. Warns rather than fails: rule 12.
    """
    from backyard_bird.images.providers import DEFAULT_PROVIDER, KNOWN_PROVIDERS

    if preferred_sources is None:
        return CheckResult("image_providers", "warn", "Skipped: no config loaded.")

    unknown = [name for name in preferred_sources if name not in KNOWN_PROVIDERS]
    known = [name for name in preferred_sources if name in KNOWN_PROVIDERS]

    if unknown and not known:
        return CheckResult(
            "image_providers",
            "warn",
            f"images.preferred_sources names only unimplemented provider(s): {', '.join(unknown)}. "
            f"Falling back to {DEFAULT_PROVIDER}. Known providers: {', '.join(KNOWN_PROVIDERS)}.",
        )
    if unknown:
        return CheckResult(
            "image_providers",
            "warn",
            f"Ignoring unimplemented provider(s) in images.preferred_sources: {', '.join(unknown)}. "
            f"Active: {', '.join(known)}. Known providers: {', '.join(KNOWN_PROVIDERS)}.",
        )
    if not known:
        return CheckResult(
            "image_providers",
            "warn",
            f"images.preferred_sources is empty — falling back to {DEFAULT_PROVIDER}.",
        )
    return CheckResult("image_providers", "pass", f"{len(known)} provider(s) configured: {', '.join(known)}")


def check_frame_configuration(photo_frame_config: object | None = None) -> CheckResult:
    """§25 "frame configuration". Builds the configured adapter and
    asks it to test its own connection (§17.4) — for local_export that
    is "is the export directory writable", which is exactly what would
    otherwise fail silently at delivery time. Warns rather than fails:
    rule 12, and `unconfigured` is a legitimate deliberate state.
    """
    if photo_frame_config is None:
        return CheckResult("frame_configuration", "warn", "Skipped: no config loaded.")

    from backyard_bird.frame.service import build_frame_adapter

    try:
        adapter = build_frame_adapter(photo_frame_config)  # type: ignore[arg-type]
        result = adapter.test_connection()
    except Exception as exc:
        return CheckResult("frame_configuration", "warn", f"Frame adapter could not be checked: {exc}")

    name = getattr(photo_frame_config, "adapter", "?")
    if not result.ok:
        return CheckResult("frame_configuration", "warn", f"Adapter {name!r}: {result.message}")
    return CheckResult("frame_configuration", "pass", f"Adapter {name!r}: {result.message}")


def check_network_access(timeout_seconds: float = 4.0) -> CheckResult:
    """§25 "network access", scoped to what this project actually needs
    the internet for: reaching the image providers. Capture and BirdNET
    are fully offline (rule 11), so this can only ever warn — a Pi with
    no uplink is still a working bird detector, just one that can't
    fetch new photos.
    """
    import socket
    import urllib.error
    import urllib.request

    endpoints = [
        ("wikimedia_commons", "https://commons.wikimedia.org/robots.txt"),
        ("inaturalist", "https://api.inaturalist.org/v1/ping"),
    ]
    reachable, unreachable = [], []
    for name, url in endpoints:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "backyard-bird-display/doctor"})
            with urllib.request.urlopen(request, timeout=timeout_seconds):
                reachable.append(name)
        except (urllib.error.URLError, socket.timeout, OSError):
            unreachable.append(name)

    if not reachable:
        return CheckResult(
            "network_access",
            "warn",
            "No image provider reachable. Audio capture and BirdNET are unaffected "
            "(they never need the network) — only new species photos will be missing "
            "until connectivity returns.",
        )
    if unreachable:
        return CheckResult(
            "network_access",
            "warn",
            f"Reachable: {', '.join(reachable)}. Not reachable: {', '.join(unreachable)}.",
        )
    return CheckResult("network_access", "pass", f"Image providers reachable: {', '.join(reachable)}")


def run_all_checks(
    data_directory: Path | None = None,
    configured_device_name: str | None = None,
    sample_wav: Path | None = None,
    migrations_dir: Path | None = None,
    preferred_image_sources: list[str] | None = None,
    photo_frame_config: object | None = None,
    check_network: bool = True,
) -> list[CheckResult]:
    """The config-derived arguments are all optional because `doctor`
    is meant to run before config.yaml exists (right after
    scripts/install.sh) — anything that needs config reports "skipped"
    rather than failing the run.
    """
    results = [check_platform(), check_python_version(), check_birdnet(sample_wav)]
    if data_directory is not None:
        results.append(check_required_directories(data_directory))
        results.append(check_disk_space(data_directory))
        results.append(check_database_access(data_directory, migrations_dir))
    results.append(check_audio_devices(configured_device_name))
    results.append(check_microphone_permission(configured_device_name))
    results.append(check_image_providers(preferred_image_sources))
    results.append(check_frame_configuration(photo_frame_config))
    if check_network:
        results.append(check_network_access())
    results.append(check_service_autostart())
    return results
