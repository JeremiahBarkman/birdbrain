"""Command-line interface for the Backyard Bird Discovery System.

See requirements §25 for the full planned subcommand list. Implemented
so far: config, audio device discovery/test, continuous capture,
one-shot and queue-driven BirdNET analysis, database migrate/
integrity-check, queue status, detection/species timeline queries,
image acquisition, the live status dashboard, `services` (install/
uninstall/status — systemd on Linux, launchd on macOS, §29 Phase 8,
added 2026-09-14), `doctor` (checks only — network access, frame
configuration, and image-provider configuration aren't covered yet),
and `setup` (an interactive first-run wizard for location and
microphone selection — not in the original §25 list, added 2026-09-14
once a public/friendlier install became a real goal; see README).
Not yet built: slideshow/frame commands (Phase 6+).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from backyard_bird.config import AppConfig, ConfigError, DashboardConfig, load_config
from backyard_bird.logging_config import configure_logging

DEFAULT_CONFIG_PATH = Path("config/config.yaml")

# migrations/ lives at the repo root, not inside the installed package —
# fine for this project (run from source on one machine, never packaged
# for distribution), but worth knowing if that assumption ever changes.
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
REPO_DIR = Path(__file__).resolve().parents[2]


def _load_config_or_exit(config_path: Path) -> AppConfig:
    try:
        return load_config(config_path)
    except ConfigError as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)


def _lan_ip() -> str | None:
    """Best-effort local LAN address, for telling the user what URL to
    type on another device — "0.0.0.0" (bind-all) isn't itself a
    usable address. Opens no real connection: UDP connect() just asks
    the OS to pick the outbound interface/source address for that
    destination, entirely locally.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _db_path(app_config: AppConfig) -> Path:
    return app_config.system.data_directory / "database" / "birds.sqlite3"


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    help="Path to config.yaml",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Path) -> None:
    """bird-display: Backyard Bird Discovery System CLI."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@cli.group("config")
def config_group() -> None:
    """Configuration commands."""


@config_group.command("validate")
@click.pass_context
def config_validate(ctx: click.Context) -> None:
    """Load and validate config.yaml."""
    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    click.echo(f"Configuration valid: {config_path}")
    click.echo(f"  timezone:   {app_config.system.timezone}")
    click.echo(f"  location:   {app_config.location.latitude}, {app_config.location.longitude}")
    click.echo(f"  microphone: {app_config.audio.device_name}")


@cli.group()
def audio() -> None:
    """Audio device commands."""


@audio.command("list-devices")
def audio_list_devices() -> None:
    """List input (microphone) devices the OS currently exposes."""
    from backyard_bird.audio.devices import list_input_devices

    devices = list_input_devices()
    if not devices:
        click.echo("No input devices found.")
        return
    for d in devices:
        click.echo(
            f"[{d.index}] {d.name}  "
            f"(channels={d.max_input_channels}, "
            f"default_rate={d.default_samplerate:.0f} Hz, "
            f"host_api={d.host_api})"
        )


@audio.command("test")
@click.option("--device", "device", default=None, help="Device name or index (default: configured device)")
@click.option("--seconds", "seconds", default=5.0, show_default=True, help="Recording duration")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=Path("data/temp/mic-test.wav"),
    show_default=True,
)
@click.pass_context
def audio_test(ctx: click.Context, device: str | None, seconds: float, output_path: Path) -> None:
    """Record a short clip from the microphone to verify capture works."""
    from backyard_bird.audio.devices import record_test_clip

    config_path: Path = ctx.obj["config_path"]
    app_config = load_config(config_path) if config_path.exists() else None

    sample_rate = app_config.audio.sample_rate if app_config else 48000
    channels = app_config.audio.channels if app_config else 1
    selected_device = device or (app_config.audio.device_name if app_config else None)

    click.echo(f"Recording {seconds:.1f}s from device={selected_device!r} at {sample_rate} Hz...")
    result_path = record_test_clip(
        device=selected_device,
        duration_seconds=seconds,
        sample_rate=sample_rate,
        channels=channels,
        output_path=output_path,
    )
    click.echo(f"Wrote test clip: {result_path}")


@cli.group()
def capture() -> None:
    """Continuous audio capture commands."""


@capture.command("run")
@click.option(
    "--max-segments",
    "max_segments",
    type=int,
    default=None,
    help="Stop after this many segments (for smoke-testing; omit for continuous production capture).",
)
@click.pass_context
def capture_run(ctx: click.Context, max_segments: int | None) -> None:
    """Continuously capture from the configured microphone into data/audio/incoming/."""
    import signal

    from backyard_bird.audio.capture_service import CaptureService

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    configure_logging(
        app_config.system.log_level,
        service_name="bird_capture",
        log_dir=app_config.system.data_directory / "logs",
    )

    incoming_dir = app_config.system.data_directory / "audio" / "incoming"
    status_path = app_config.system.data_directory / "run" / "mic_status.json"
    gain_control_path = app_config.system.data_directory / "run" / "mic_gain.json"
    device_control_path = app_config.system.data_directory / "run" / "mic_device.json"
    live_monitor_port = app_config.audio.live_monitor_port if app_config.audio.enable_live_monitor else None
    service = CaptureService(
        app_config.audio,
        incoming_dir,
        status_path=status_path,
        live_monitor_port=live_monitor_port,
        gain_control_path=gain_control_path,
        device_control_path=device_control_path,
    )

    def _handle_signal(signum: int, frame: object) -> None:
        click.echo("\nStopping capture...")
        service.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    click.echo(f"Capturing from {app_config.audio.device_name!r} into {incoming_dir} (Ctrl-C to stop)...")
    segments_written = service.run(max_segments=max_segments)
    click.echo(f"Wrote {segments_written} segment(s).")


@cli.group()
def analyze() -> None:
    """BirdNET analysis commands."""


@analyze.command("file")
@click.argument("audio_path", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def analyze_file_cmd(ctx: click.Context, audio_path: Path) -> None:
    """Run BirdNET-Analyzer on a single WAV file and print detections."""
    from backyard_bird.analysis.birdnet_adapter import analyze_file

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    configure_logging(app_config.system.log_level)

    click.echo(f"Analyzing {audio_path} ...")
    result = analyze_file(
        audio_path=audio_path,
        birdnet_config=app_config.birdnet,
        location=app_config.location,
    )

    click.echo(
        f"BirdNET version: {result.birdnet_version}  "
        f"Duration: {result.analysis_duration_seconds:.2f}s  "
        f"Detections: {len(result.detections)}"
    )
    for det in sorted(result.detections, key=lambda d: -d.confidence):
        click.echo(
            f"  {det.confidence:.2f}  {det.common_name} ({det.scientific_name})  "
            f"[{det.start_time_seconds:.1f}s-{det.end_time_seconds:.1f}s]"
        )


@analyze.command("run")
@click.option(
    "--max-files",
    "max_files",
    type=int,
    default=None,
    help="Stop after this many files (for smoke-testing; omit for continuous production use).",
)
@click.pass_context
def analyze_run(ctx: click.Context, max_files: int | None) -> None:
    """Continuously watch data/audio/incoming/ and analyze new segments."""
    import signal
    import threading

    from backyard_bird.analysis.worker import QueueDirs, run_worker_loop
    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.migrations import apply_migrations

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    configure_logging(
        app_config.system.log_level,
        service_name="bird_analyzer",
        log_dir=app_config.system.data_directory / "logs",
    )

    conn = get_connection(_db_path(app_config))
    apply_migrations(conn, MIGRATIONS_DIR)
    dirs = QueueDirs.under(app_config.system.data_directory)
    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        click.echo("\nStopping analyzer...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    click.echo(f"Watching {dirs.incoming} (Ctrl-C to stop)...")
    processed = run_worker_loop(
        conn,
        app_config.birdnet,
        app_config.location,
        dirs,
        stop_event,
        app_config.detections,
        audio_config=app_config.audio,
        max_files=max_files,
    )
    conn.close()
    click.echo(f"Processed {processed} file(s).")


@cli.group()
def queue() -> None:
    """Audio processing queue commands."""


@queue.command("status")
@click.pass_context
def queue_status(ctx: click.Context) -> None:
    """Show how many segments are waiting in each queue stage."""
    from backyard_bird.analysis.worker import QueueDirs

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    dirs = QueueDirs.under(app_config.system.data_directory)

    for name, directory in (
        ("incoming", dirs.incoming),
        ("processing", dirs.processing),
        ("processed", dirs.processed),
        ("failed", dirs.failed),
    ):
        count = len(list(directory.glob("*.wav"))) if directory.exists() else 0
        click.echo(f"{name:<11} {count}")


@cli.group("database")
def database_group() -> None:
    """Database commands."""


@database_group.command("migrate")
@click.pass_context
def database_migrate(ctx: click.Context) -> None:
    """Apply any pending schema migrations."""
    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.migrations import apply_migrations

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    conn = get_connection(_db_path(app_config))
    applied = apply_migrations(conn, MIGRATIONS_DIR)
    conn.close()
    if applied:
        click.echo(f"Applied migrations: {applied}")
    else:
        click.echo("Database already up to date.")


@database_group.command("integrity-check")
@click.pass_context
def database_integrity_check(ctx: click.Context) -> None:
    """Run SQLite's built-in integrity check."""
    from backyard_bird.database.connection import get_connection

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    conn = get_connection(_db_path(app_config))
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    if result == "ok":
        click.echo("Database integrity: ok")
    else:
        click.echo(f"Database integrity problems found:\n{result}", err=True)
        sys.exit(1)


def _print_detections(rows: list) -> None:
    if not rows:
        click.echo("No detections found.")
        return
    for r in rows:
        click.echo(f"{r.detected_at_utc}  {r.confidence:.2f}  {r.common_name} ({r.scientific_name})")


@cli.group()
def detections() -> None:
    """Detection timeline commands."""


@detections.command("today")
@click.pass_context
def detections_today(ctx: click.Context) -> None:
    """List today's detections (local time, per config.system.timezone)."""
    from zoneinfo import ZoneInfo

    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.repositories import list_detections

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    tz = ZoneInfo(app_config.system.timezone)
    start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    conn = get_connection(_db_path(app_config))
    rows = list_detections(conn, since_utc=start_local.astimezone(timezone.utc), limit=500)
    conn.close()
    _print_detections(rows)


@detections.command("list")
@click.option("--date", "date_str", default=None, help="YYYY-MM-DD, local time")
@click.option("--species", "species_query", default=None, help="Filter by common/scientific name substring")
@click.option("--limit", default=100, show_default=True)
@click.pass_context
def detections_list(
    ctx: click.Context, date_str: str | None, species_query: str | None, limit: int
) -> None:
    """List detections, optionally filtered by date and/or species."""
    from zoneinfo import ZoneInfo

    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.repositories import list_detections

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)

    since_utc = None
    until_utc = None
    if date_str:
        tz = ZoneInfo(app_config.system.timezone)
        day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
        since_utc = day.astimezone(timezone.utc)
        until_utc = (day + timedelta(days=1)).astimezone(timezone.utc)

    conn = get_connection(_db_path(app_config))
    rows = list_detections(
        conn, since_utc=since_utc, until_utc=until_utc, species_query=species_query, limit=limit
    )
    conn.close()
    _print_detections(rows)


@cli.group()
def species() -> None:
    """Species commands."""


@species.command("list")
@click.pass_context
def species_list(ctx: click.Context) -> None:
    """List every species detected so far, with counts and first/last seen."""
    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.repositories import list_species_summary

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    conn = get_connection(_db_path(app_config))
    rows = list_species_summary(conn)
    conn.close()

    if not rows:
        click.echo("No species recorded yet.")
        return
    for r in rows:
        click.echo(
            f"{r.common_name} ({r.scientific_name})  count={r.detection_count}  "
            f"first={r.first_detected_at_utc}  last={r.last_detected_at_utc}"
        )


def _resolve_dashboard_host_port(
    cli_host: str | None, cli_port: int | None, dashboard_config: DashboardConfig
) -> tuple[str, int]:
    """CLI flags override config.yaml's dashboard.host/port when given,
    so `dashboard run` alone (as used by start_all.sh) picks up
    whatever's configured — e.g. LAN access — without needing the
    desktop launcher to know about it.
    """
    return cli_host or dashboard_config.host, cli_port or dashboard_config.port


@cli.group()
def dashboard() -> None:
    """Local status dashboard (§22)."""


@dashboard.command("url")
@click.pass_context
def dashboard_url(ctx: click.Context) -> None:
    """Print the dashboard's actual configured URL.

    Exists so scripts/start_all.sh can show the real address instead
    of a hardcoded guess — it used to always print
    http://127.0.0.1:8765 regardless of dashboard.host, which was
    simply wrong the moment host was set to 0.0.0.0 for LAN access
    (confirmed live on the Raspberry Pi: the dashboard itself logged
    the correct LAN URL while start_all.sh's own summary line lied
    about it). Reuses the same resolution logic `dashboard run` uses,
    rather than a second copy in bash (CLAUDE.md: no business logic in
    shell scripts).
    """
    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    host, port = _resolve_dashboard_host_port(None, None, app_config.dashboard)
    if host in ("127.0.0.1", "localhost"):
        click.echo(f"http://{host}:{port}")
        return
    lan_ip = _lan_ip() if host == "0.0.0.0" else host
    click.echo(f"http://{lan_ip or host}:{port}")


@dashboard.command("run")
@click.option(
    "--host",
    default=None,
    help="Overrides dashboard.host in config.yaml (default: 127.0.0.1, localhost-only per §23.3)",
)
@click.option("--port", default=None, type=int, help="Overrides dashboard.port in config.yaml")
@click.option(
    "--reload",
    "reload_",
    is_flag=True,
    default=False,
    help=(
        "Auto-restart the server when a .py file under src/ changes — for iterating on "
        "routes.py/repositories.py without manually restarting. Off by default: this is "
        "the watchdog reloader (file-watch + respawn), not Flask's interactive debugger, "
        "so it's still fine to point at 0.0.0.0, but it's dev-workflow behavior, not "
        "something start_all.sh's always-on service should carry."
    ),
)
@click.pass_context
def dashboard_run(ctx: click.Context, host: str | None, port: int | None, reload_: bool) -> None:
    """Serve the live stats dashboard over HTTP."""
    from backyard_bird.web.app import create_app

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    configure_logging(app_config.system.log_level, service_name="bird_status")

    host, port = _resolve_dashboard_host_port(host, port, app_config.dashboard)

    app = create_app(app_config, config_path=config_path)
    click.echo(f"Dashboard running on http://{host}:{port}  (Ctrl-C to stop)")
    if host not in ("127.0.0.1", "localhost"):
        # 0.0.0.0 means "every interface" — not itself a usable URL, so
        # show the machine's actual LAN address for other devices to use.
        lan_ip = _lan_ip() if host == "0.0.0.0" else host
        if lan_ip:
            click.echo(f"  From other devices on your network: http://{lan_ip}:{port}")
    if reload_:
        click.echo("  --reload: watching .py files, will restart on change")
    app.run(host=host, port=port, debug=False, use_reloader=reload_)


def _build_image_providers(preferred_sources: list[str]) -> list:
    """Builds the provider list from images.preferred_sources (§18.1),
    so config controls which sources get tried and in what order —
    not something hardcoded here. Falls back to Wikimedia alone if the
    config list is empty or names nothing this codebase implements.
    """
    from backyard_bird.images.providers.inaturalist import INaturalistProvider
    from backyard_bird.images.providers.wikimedia import WikimediaCommonsProvider

    registry = {
        "wikimedia_commons": WikimediaCommonsProvider,
        "inaturalist": INaturalistProvider,
    }
    providers = [registry[name]() for name in preferred_sources if name in registry]
    return providers or [WikimediaCommonsProvider()]


@cli.group()
def images() -> None:
    """Bird image acquisition commands (§15)."""


@images.command("fetch-missing")
@click.pass_context
def images_fetch_missing(ctx: click.Context) -> None:
    """Search for images for every species that doesn't have one yet."""
    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.migrations import apply_migrations
    from backyard_bird.database.repositories import list_species_needing_image_search
    from backyard_bird.images.service import acquire_image_for_species

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    configure_logging(app_config.system.log_level, service_name="bird_images")

    conn = get_connection(_db_path(app_config))
    apply_migrations(conn, MIGRATIONS_DIR)
    species_rows = list_species_needing_image_search(conn, app_config.images.refresh_after_days)
    if not species_rows:
        click.echo("Every species already has an image or a recorded unavailable status.")
        conn.close()
        return

    providers = _build_image_providers(app_config.images.preferred_sources)
    images_root = app_config.system.data_directory / "images"

    for row in species_rows:
        click.echo(f"Searching for {row['common_name']} ({row['scientific_name']})...")
        status = acquire_image_for_species(
            conn, row["id"], row["scientific_name"], row["common_name"], providers, app_config.images, images_root
        )
        click.echo(f"  -> {status}")

    conn.close()


@images.command("refresh")
@click.argument("scientific_name")
@click.pass_context
def images_refresh(ctx: click.Context, scientific_name: str) -> None:
    """Force a fresh image search for one species (by scientific name)."""
    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.migrations import apply_migrations
    from backyard_bird.images.service import acquire_image_for_species

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    configure_logging(app_config.system.log_level, service_name="bird_images")

    conn = get_connection(_db_path(app_config))
    apply_migrations(conn, MIGRATIONS_DIR)
    row = conn.execute(
        "SELECT id, scientific_name, common_name FROM species WHERE scientific_name = ?",
        (scientific_name,),
    ).fetchone()
    if row is None:
        click.echo(f"No species found with scientific name {scientific_name!r}", err=True)
        conn.close()
        sys.exit(1)

    providers = _build_image_providers(app_config.images.preferred_sources)
    images_root = app_config.system.data_directory / "images"
    status = acquire_image_for_species(
        conn, row["id"], row["scientific_name"], row["common_name"], providers, app_config.images, images_root
    )
    click.echo(f"{row['common_name']}: {status}")
    conn.close()


@images.command("watch")
@click.option(
    "--interval-seconds",
    "interval_seconds",
    type=float,
    default=600.0,
    show_default=True,
    help="How often to check for species that still need an image.",
)
@click.pass_context
def images_watch(ctx: click.Context, interval_seconds: float) -> None:
    """Continuously search for images as new species without one appear (§15.1)."""
    import signal
    import threading

    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.migrations import apply_migrations
    from backyard_bird.images.service import run_watch_loop

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    configure_logging(app_config.system.log_level, service_name="bird_images_watch")

    conn = get_connection(_db_path(app_config))
    apply_migrations(conn, MIGRATIONS_DIR)
    providers = _build_image_providers(app_config.images.preferred_sources)
    images_root = app_config.system.data_directory / "images"
    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        click.echo("\nStopping image watcher...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    click.echo(f"Watching for new species needing images (every {interval_seconds:.0f}s, Ctrl-C to stop)...")
    run_watch_loop(
        conn, providers, app_config.images, images_root, stop_event, poll_interval_seconds=interval_seconds
    )
    conn.close()


@cli.group()
def recordings() -> None:
    """Best-recording clip commands (§12.4's design-pivot note)."""


@recordings.command("backfill-spectrograms")
@click.pass_context
def recordings_backfill_spectrograms(ctx: click.Context) -> None:
    """Generate spectrogram.png for every existing best_recordings clip.

    One-off catch-up for clips extracted before spectrogram generation
    existed (added 2026-09-18) — new clips get one automatically as
    part of analyze run/file (see analysis/worker.py). Safe to re-run:
    it just regenerates and overwrites each spectrogram.png in place.
    """
    from backyard_bird.audio.clips import species_spectrogram_path
    from backyard_bird.audio.spectrogram import generate_spectrogram
    from backyard_bird.database.connection import get_connection
    from backyard_bird.database.migrations import apply_migrations
    from backyard_bird.database.repositories import get_best_recordings_by_scientific_name

    config_path: Path = ctx.obj["config_path"]
    app_config = _load_config_or_exit(config_path)
    configure_logging(app_config.system.log_level, service_name="bird_recordings")

    conn = get_connection(_db_path(app_config))
    apply_migrations(conn, MIGRATIONS_DIR)
    best_recordings = get_best_recordings_by_scientific_name(conn)
    conn.close()

    if not best_recordings:
        click.echo("No best_recordings clips found.")
        return

    best_clips_root = app_config.system.data_directory / "audio" / "best_clips"
    succeeded = 0
    for scientific_name, row in best_recordings.items():
        clip_path = Path(row["clip_path"])
        dest_path = species_spectrogram_path(best_clips_root, scientific_name)
        try:
            generate_spectrogram(clip_path, dest_path)
        except Exception as exc:  # noqa: BLE001 — one bad clip must not stop the rest
            click.echo(f"  {scientific_name}: FAILED ({exc})")
            continue
        succeeded += 1
        click.echo(f"  {scientific_name}: OK")

    click.echo(f"Backfilled {succeeded}/{len(best_recordings)} spectrograms.")


_STATUS_MARKERS = {"pass": "OK  ", "warn": "WARN", "fail": "FAIL"}

# Repo-root-relative so this works whether bird-display is run from a
# fresh clone (installer's prerequisite gate, before config.yaml even
# necessarily reflects a real microphone) or a configured install.
SAMPLE_AUDIO_DIR = Path(__file__).resolve().parents[2] / "tests" / "sample_audio"


@cli.command("doctor")
@click.pass_context
def doctor_cmd(ctx: click.Context) -> None:
    """Check that the environment is ready to run the system (§25).

    Runs without a valid config.yaml (useful right after
    scripts/install.sh, before you've edited it) — directory/disk and
    configured-device checks are simply skipped or run unconfigured.
    """
    from backyard_bird.doctor import run_all_checks

    config_path: Path = ctx.obj["config_path"]
    app_config: AppConfig | None = None
    config_error: str | None = None
    if config_path.exists():
        try:
            app_config = load_config(config_path)
        except ConfigError as exc:
            config_error = str(exc)

    if config_error:
        click.echo(f"[{_STATUS_MARKERS['warn']}] config: {config_error}")

    sample_candidates = sorted(SAMPLE_AUDIO_DIR.glob("*.wav")) if SAMPLE_AUDIO_DIR.exists() else []
    sample_wav = sample_candidates[0] if sample_candidates else None

    results = run_all_checks(
        data_directory=app_config.system.data_directory if app_config else None,
        configured_device_name=app_config.audio.device_name if app_config else None,
        sample_wav=sample_wav,
    )

    for result in results:
        click.echo(f"[{_STATUS_MARKERS[result.status]}] {result.name}: {result.message}")

    if any(r.status == "fail" for r in results):
        sys.exit(1)


@cli.command("setup")
@click.pass_context
def setup_cmd(ctx: click.Context) -> None:
    """Interactive first-run setup: region and microphone.

    Both fields this walks through fail *silently* rather than loudly
    if left at their config.example.yaml placeholder values (or, for
    the microphone on Linux, after a reboot renumbers its ALSA index -
    see requirements §31.1): capture retries forever with no crash,
    and geographic filtering just quietly excludes real local species.
    This exists so a new install doesn't have to discover either the
    hard way.
    """
    from backyard_bird.audio.devices import list_input_devices
    from backyard_bird.setup_wizard import geocode_location, set_config_value

    config_path: Path = ctx.obj["config_path"]
    example_path = config_path.parent / "config.example.yaml"

    if not config_path.exists():
        if not example_path.exists():
            click.echo(f"Neither {config_path} nor {example_path} exists.", err=True)
            sys.exit(1)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(example_path.read_text())
        click.echo(f"Created {config_path} from the example.")

    click.echo()
    click.echo("=== Location (BirdNET's geographic species filter, §10.3) ===")
    click.echo("'City, State' (e.g. 'Portland, OR') is least ambiguous — a bare ZIP/postal")
    click.echo("code can match a different country (confirmed live: a real US ZIP once")
    click.echo("matched a location in Lithuania instead). Always confirm before saving, below.")
    while True:
        place = click.prompt(
            "City/state or ZIP near the microphone (blank to skip)", default="", show_default=False
        )
        if not place.strip():
            click.echo("Skipped — edit location.latitude/longitude in config.yaml by hand.")
            break
        result = geocode_location(place)
        if result is None:
            click.echo("Couldn't find that place (or the lookup failed — check network access).")
            if click.confirm("Try again?", default=True):
                continue
            click.echo("Skipped — edit location.latitude/longitude in config.yaml by hand.")
            break
        click.echo(f"Found: {result.display_name}")
        click.echo(f"  latitude:  {result.latitude}")
        click.echo(f"  longitude: {result.longitude}")
        if not click.confirm("Use this location?", default=True):
            continue
        ok_lat = set_config_value(config_path, "location", "latitude", result.latitude)
        ok_lon = set_config_value(config_path, "location", "longitude", result.longitude)
        if ok_lat and ok_lon:
            click.echo("Saved. (Location data © OpenStreetMap contributors.)")
        else:
            click.echo("Could not write to config.yaml — edit location.latitude/longitude by hand.", err=True)
        break

    click.echo()
    click.echo("=== Microphone (§8.1) ===")
    devices = list_input_devices()
    if not devices:
        click.echo(
            "No input devices found. Plug in a microphone and re-run `bird-display setup`. "
            "On Linux, also check that `bird-display capture run` isn't already running — "
            "ALSA's raw device nodes only allow one process at a time to even query them, "
            "so an active capture service alone can make this look like no mic is connected "
            "(stop it first with ./scripts/stop_all.sh, then re-run setup). Or edit "
            "audio.device_name in config.yaml by hand once a device is available."
        )
    else:
        if len(devices) == 1:
            chosen = devices[0]
            click.echo(f"Found one input device: {chosen.name}")
            use_it = click.confirm("Use this microphone?", default=True)
        else:
            click.echo("Multiple input devices found:")
            for d in devices:
                click.echo(f"  [{d.index}] {d.name}")
            choice = click.prompt("Which device? (number, blank to skip)", default="", show_default=False)
            chosen = next((d for d in devices if str(d.index) == choice.strip()), None) if choice.strip() else None
            use_it = chosen is not None

        if use_it and chosen is not None:
            if set_config_value(config_path, "audio", "device_name", chosen.name):
                click.echo("Saved.")
            else:
                click.echo("Could not write to config.yaml — edit audio.device_name by hand.", err=True)
        else:
            click.echo("Skipped — edit audio.device_name in config.yaml by hand.")

    click.echo()
    click.echo("Setup complete. Run `bird-display doctor` to verify, or")
    click.echo("`bird-display config validate` to see what's now configured.")


def _venv_bin() -> Path:
    """The bin/ directory of whatever Python is actually running this
    command — works whether that's .venv/bin or something else,
    without assuming a fixed venv location.

    Deliberately NOT .resolve()'d: a venv's python binary is typically
    a symlink to the base interpreter that created it (e.g.
    .venv/bin/python3.11 -> /usr/bin/python3.11) — resolving it
    follows that symlink to its real target, which is outside the venv
    entirely. Confirmed live on the Pi: this exact mistake wrote
    ExecStart=/usr/bin/bird-display into a systemd unit instead of the
    venv's own bird-display script — systemd could never execute it
    (203/EXEC), so capture crash-looped on every restart attempt and
    never actually ran (see README's auto-start notes).
    """
    return Path(sys.executable).parent


@cli.group()
def services() -> None:
    """Boot-time auto-start (systemd on Linux, launchd on macOS; §29 Phase 8).

    Installs the four long-running services (capture, analyzer,
    images-watch, dashboard) as native OS services so they survive a
    reboot without scripts/start_all.sh being run by hand. Each is its
    own independent unit/agent with its own restart policy — one
    crashing repeatedly does not stop the others (§20.1).
    """


@services.command("install")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt (for scripted/non-interactive use).")
def services_install(yes: bool) -> None:
    """Install and enable auto-start for this OS.

    Requires sudo on Linux (writes /etc/systemd/system/ unit files —
    a system-level choice specifically to avoid needing `loginctl
    enable-linger` for services to start before any login on a
    headless boot). No sudo on macOS (LaunchAgents live under the
    user's own ~/Library/LaunchAgents/) — but per §31.1, a LaunchAgent
    is required there specifically, not a LaunchDaemon, or microphone
    capture would silently fail with no GUI session attached.
    """
    import platform
    import subprocess

    from backyard_bird.service_install import (
        SERVICE_DEFINITIONS,
        launchd_plist_filename,
        render_launchd_plist,
        render_systemd_unit,
        systemd_unit_filename,
    )

    system = platform.system()
    venv_bin = _venv_bin()

    if system == "Darwin":
        agents_dir = Path.home() / "Library" / "LaunchAgents"
        click.echo(f"Will install {len(SERVICE_DEFINITIONS)} LaunchAgents to {agents_dir}:")
        for service in SERVICE_DEFINITIONS:
            click.echo(f"  {service.name}")
        if not yes and not click.confirm("Install and start these now?", default=True):
            click.echo("Aborted — nothing changed.")
            return
        agents_dir.mkdir(parents=True, exist_ok=True)
        (REPO_DIR / "data" / "logs").mkdir(parents=True, exist_ok=True)
        for service in SERVICE_DEFINITIONS:
            plist_path = agents_dir / launchd_plist_filename(service)
            plist_path.write_bytes(render_launchd_plist(service, repo_dir=REPO_DIR, venv_bin=venv_bin))
            import os

            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
                check=False,
            )
            click.echo(f"  {service.name}: installed and started")
        click.echo("Done. `bird-display services status` to check.")
    elif system == "Linux":
        import getpass

        units_dir = Path("/etc/systemd/system")
        user = getpass.getuser()
        click.echo(f"Will install {len(SERVICE_DEFINITIONS)} systemd services to {units_dir} (needs sudo):")
        for service in SERVICE_DEFINITIONS:
            click.echo(f"  {systemd_unit_filename(service)}")
        if not yes and not click.confirm("Install and enable these now?", default=True):
            click.echo("Aborted — nothing changed.")
            return
        (REPO_DIR / "data" / "logs").mkdir(parents=True, exist_ok=True)
        unit_names = []
        for service in SERVICE_DEFINITIONS:
            content = render_systemd_unit(service, repo_dir=REPO_DIR, venv_bin=venv_bin, user=user)
            unit_name = systemd_unit_filename(service)
            unit_names.append(unit_name)
            subprocess.run(
                ["sudo", "tee", str(units_dir / unit_name)],
                input=content,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
            )
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "--now", *unit_names], check=True)
        click.echo("Done. `bird-display services status` to check.")
    else:
        click.echo(f"Auto-start isn't supported on {system}.", err=True)
        sys.exit(1)


@services.command("uninstall")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def services_uninstall(yes: bool) -> None:
    """Stop, disable, and remove the auto-start services."""
    import platform
    import subprocess

    from backyard_bird.service_install import SERVICE_DEFINITIONS, launchd_plist_filename, systemd_unit_filename

    if not yes and not click.confirm("Stop and remove all auto-start services?", default=False):
        click.echo("Aborted — nothing changed.")
        return

    system = platform.system()
    if system == "Linux":
        unit_names = [systemd_unit_filename(s) for s in SERVICE_DEFINITIONS]
        subprocess.run(["sudo", "systemctl", "disable", "--now", *unit_names], check=False)
        for unit_name in unit_names:
            subprocess.run(["sudo", "rm", "-f", f"/etc/systemd/system/{unit_name}"], check=False)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
    elif system == "Darwin":
        import os

        agents_dir = Path.home() / "Library" / "LaunchAgents"
        for service in SERVICE_DEFINITIONS:
            plist_path = agents_dir / launchd_plist_filename(service)
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)], check=False)
            plist_path.unlink(missing_ok=True)
    else:
        click.echo(f"Auto-start isn't supported on {system}.", err=True)
        sys.exit(1)
    click.echo("Done.")


@services.command("status")
def services_status() -> None:
    """Show whether each service is installed as an OS auto-start service, and its current state."""
    import platform
    import subprocess

    from backyard_bird.service_install import SERVICE_DEFINITIONS, launchd_plist_filename, systemd_unit_filename

    system = platform.system()
    for service in SERVICE_DEFINITIONS:
        if system == "Linux":
            unit_name = systemd_unit_filename(service)
            if not Path("/etc/systemd/system", unit_name).exists():
                state = "not installed"
            else:
                result = subprocess.run(["systemctl", "is-active", unit_name], capture_output=True, text=True)
                state = result.stdout.strip() or "unknown"
        elif system == "Darwin":
            plist_path = Path.home() / "Library" / "LaunchAgents" / launchd_plist_filename(service)
            if not plist_path.exists():
                state = "not installed"
            else:
                result = subprocess.run(
                    ["launchctl", "list", launchd_plist_filename(service).removesuffix(".plist")],
                    capture_output=True,
                    text=True,
                )
                state = "running" if result.returncode == 0 else "not running"
        else:
            state = "unsupported OS"
        click.echo(f"  {service.name:15s} {state}")


if __name__ == "__main__":
    cli()
