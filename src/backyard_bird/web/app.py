"""Flask app factory for the local status dashboard (§22)."""
from __future__ import annotations

from pathlib import Path

from flask import Flask

from backyard_bird.config import AppConfig


def create_app(app_config: AppConfig, config_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    # Jinja2 caches compiled templates by default when debug=False (which
    # `dashboard run` always uses — no reason to run Flask's debugger on a
    # local status page). Without this, editing index.html silently has no
    # effect until the process restarts — confirmed live while building the
    # image-popup feature. Static files (CSS/JS) aren't affected by this;
    # Flask serves those fresh from disk on every request regardless.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    # .resolve() matters here, not just tidiness: Flask's send_from_directory
    # (used to serve cached species images) treats a relative directory as
    # relative to the app's own module path (src/backyard_bird/web/), not
    # the process's working directory — a relative data_directory silently
    # 404'd every image until this was made absolute (confirmed live).
    data_directory = app_config.system.data_directory.resolve()
    app.config["DB_PATH"] = data_directory / "database" / "birds.sqlite3"
    app.config["TIMEZONE"] = app_config.system.timezone
    app.config["IMAGES_ROOT"] = data_directory / "images"
    app.config["AUDIO_CLIPS_ROOT"] = data_directory / "audio" / "best_clips"
    app.config["MIC_STATUS_PATH"] = data_directory / "run" / "mic_status.json"
    app.config["LIVE_MONITOR_ENABLED"] = app_config.audio.enable_live_monitor
    app.config["LIVE_MONITOR_PORT"] = app_config.audio.live_monitor_port
    app.config["AUDIO_SAMPLE_RATE"] = app_config.audio.sample_rate
    app.config["AUDIO_CHANNELS"] = app_config.audio.channels
    app.config["GAIN_CONTROL_PATH"] = data_directory / "run" / "mic_gain.json"
    app.config["GAIN_DEFAULT"] = app_config.audio.gain
    app.config["DEVICE_CONTROL_PATH"] = data_directory / "run" / "mic_device.json"
    app.config["DEVICE_DEFAULT"] = app_config.audio.device_name
    # Fullscreen slideshow preview (§16, user request): the same
    # qualification/ordering/presentation config the eventual frame
    # delivery pipeline (§29 Phase 5) will use, applied here to a live
    # query instead of the not-yet-scheduled daily_species_summary
    # table — see /api/slideshow in routes.py.
    app.config["SLIDESHOW_MIN_CONFIDENCE"] = app_config.birdnet.slideshow_minimum_confidence
    app.config["SLIDESHOW_ORDER"] = app_config.slideshow.order
    app.config["SLIDESHOW_DISPLAY_MODE"] = app_config.slideshow.display_mode
    app.config["SLIDESHOW_IMAGE_DURATION_SECONDS"] = app_config.slideshow.image_duration_seconds
    # "Save to SD" export (§29 Phase 5's builder, user request): renders
    # a real slideshow for today into data/slideshows/<date>/ — the
    # same directory the frame delivery pipeline will eventually read
    # from too — and /api/slideshow/export (routes.py) serves those
    # files individually for the browser to download into the
    # viewer's own Downloads folder.
    app.config["SLIDESHOW_OUTPUT_ROOT"] = data_directory / "slideshows"
    # Optional: without it (e.g. most existing tests), a device switch
    # still takes effect live via DEVICE_CONTROL_PATH above, it just
    # isn't persisted back into config.yaml for the next full restart.
    app.config["CONFIG_PATH"] = config_path

    from backyard_bird.web.routes import bp

    app.register_blueprint(bp)
    return app
