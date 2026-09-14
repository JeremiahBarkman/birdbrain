"""Flask app factory for the local status dashboard (§22)."""
from __future__ import annotations

from flask import Flask

from backyard_bird.config import AppConfig


def create_app(app_config: AppConfig) -> Flask:
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

    from backyard_bird.web.routes import bp

    app.register_blueprint(bp)
    return app
