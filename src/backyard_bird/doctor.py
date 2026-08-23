"""Environment/health checks backing `bird-display doctor` (§25).

Each check is a plain function returning a CheckResult so the CLI can
just format and print them — no business logic in cli.py or in
scripts/install.sh (CLAUDE.md rule: don't put business logic in shell
scripts). install.sh runs `bird-display doctor` as its final
prerequisite gate after setting up the venv.

Not every §25 doctor item is implemented yet (network access, frame
configuration, image-provider configuration, launchd service
definitions) — those are Phase 4+/8 concerns tied to features not yet
built. What's here covers what actually gates a fresh install:
platform/Python/BirdNET/directories/disk/audio devices/microphone
permission.
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


def check_platform() -> CheckResult:
    """This project assumes macOS throughout (CoreAudio device names,
    the launcher .command files, launchd for Phase 8) — catch a
    non-macOS run immediately rather than let someone burn ten minutes
    on a tensorflow install that was never going to finish working.
    """
    import platform

    system = platform.system()
    if system != "Darwin":
        return CheckResult(
            "platform",
            "fail",
            f"Detected {system}, but this project targets macOS. Audio capture, the "
            "installer, and the desktop launcher all assume macOS.",
        )

    machine = platform.machine()
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
    subdirs = [
        "audio/incoming",
        "audio/processing",
        "audio/processed",
        "audio/failed",
        "audio/clips",
        "database",
        "images",
        "slideshows",
        "frame-export",
        "logs",
        "temp",
    ]
    failed = []
    for sub in subdirs:
        path = data_directory / sub
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            failed.append(sub)

    if failed:
        return CheckResult(
            "required_directories",
            "fail",
            f"Could not create: {', '.join(failed)} under {data_directory}",
        )
    return CheckResult(
        "required_directories", "pass", f"All {len(subdirs)} data subdirectories exist under {data_directory}"
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
    from backyard_bird.audio.devices import list_input_devices

    try:
        devices = list_input_devices()
    except Exception as exc:
        return CheckResult("audio_devices", "fail", f"Could not query audio devices: {exc}")

    if not devices:
        return CheckResult(
            "audio_devices",
            "fail",
            "No input (microphone) devices found. Check macOS microphone permissions "
            "for your terminal/Python and that a microphone is connected.",
        )

    names = [d.name for d in devices]
    if configured_device_name and configured_device_name not in names:
        return CheckResult(
            "audio_devices",
            "warn",
            f"Configured audio.device_name {configured_device_name!r} not found. "
            f"Available devices: {', '.join(names)}. Run `bird-display audio list-devices`.",
        )
    return CheckResult("audio_devices", "pass", f"{len(devices)} input device(s) found: {', '.join(names)}")


def check_microphone_permission(configured_device_name: str | None = None) -> CheckResult:
    """Distinct from check_audio_devices: listing devices just queries
    CoreAudio's device table and needs no permission, but actually
    opening a stream does (macOS's per-app microphone TCC grant) — the
    §25 "microphone permissions" item is separate from "audio-device
    availability" for exactly this reason. Attempts the same short
    real capture `bird-display audio test` does, so a denied
    permission shows up here instead of when capture starts.
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

    try:
        frames = sd.rec(int(0.3 * sample_rate), samplerate=sample_rate, channels=1, dtype="int16", device=device)
        sd.wait()
    except Exception as exc:
        return CheckResult(
            "microphone_permission",
            "fail",
            f"Could not open the microphone: {exc}. On macOS: System Settings > "
            "Privacy & Security > Microphone, and grant access to whatever runs "
            "this (Terminal/iTerm) — if it isn't listed there yet, run "
            "`bird-display audio test` once to trigger the permission prompt.",
        )

    peak = int(np.abs(frames).max()) if frames.size else 0
    if peak == 0:
        return CheckResult(
            "microphone_permission",
            "warn",
            "Captured 0.3s of complete silence. Most likely `bird-display capture run` "
            "is already using this device (some USB mics hand a second, concurrent "
            "listener silence instead of an error rather than truly sharing the "
            "stream) — harmless if so. Otherwise: a genuinely quiet room, or macOS "
            "silently denying microphone access without raising an error (it does "
            "this on some versions). Run `bird-display audio test` on its own, with "
            "capture stopped, while making noise near the mic to tell which.",
        )
    return CheckResult("microphone_permission", "pass", "Captured real audio from the microphone.")


def run_all_checks(
    data_directory: Path | None = None,
    configured_device_name: str | None = None,
    sample_wav: Path | None = None,
) -> list[CheckResult]:
    results = [check_platform(), check_python_version(), check_birdnet(sample_wav)]
    if data_directory is not None:
        results.append(check_required_directories(data_directory))
        results.append(check_disk_space(data_directory))
    results.append(check_audio_devices(configured_device_name))
    results.append(check_microphone_permission(configured_device_name))
    return results
