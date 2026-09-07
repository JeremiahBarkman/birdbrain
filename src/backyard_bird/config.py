"""Configuration loading and validation.

Configuration lives outside source code (config/config.yaml, copied
from config/config.example.yaml). This module is the single place
that knows the config file's shape — other modules should receive an
AppConfig instance rather than reading YAML themselves.

See Backyard_Bird_Discovery_System_Requirements.md §18.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError


class ConfigError(Exception):
    """Raised when the configuration file is missing, unreadable, or invalid."""


class SystemConfig(BaseModel):
    timezone: str = "UTC"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    data_directory: Path = Path("./data")


class LocationConfig(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class AudioConfig(BaseModel):
    device_name: str
    microphone_id: str
    sample_rate: int = 48000
    channels: int = Field(default=1, ge=1, le=2)
    segment_seconds: float = 30
    overlap_seconds: float = 3
    raw_audio_retention_days: int = 7
    processed_audio_retention_days: int = 7
    failed_audio_retention_days: int = 30


class BirdNETConfig(BaseModel):
    database_minimum_confidence: float = Field(default=0.60, ge=0, le=1)
    slideshow_minimum_confidence: float = Field(default=0.75, ge=0, le=1)
    sensitivity: float = 1.0
    geographic_filter_enabled: bool = True
    max_workers: int = Field(default=1, ge=1)
    retain_raw_results: bool = True
    duplicate_window_seconds: float = 5


class DetectionsConfig(BaseModel):
    extract_detection_clips: bool = True
    clip_padding_seconds: float = 1
    retain_clips_days: int = 30


class ImagesConfig(BaseModel):
    preferred_sources: list[str] = Field(
        default_factory=lambda: ["wikimedia_commons", "inaturalist"]
    )
    minimum_width: int = 1280
    minimum_height: int = 720
    images_per_species: int = 3
    refresh_after_days: int = 90
    require_license_metadata: bool = True


class SlideshowConfig(BaseModel):
    update_interval_minutes: int = 30
    one_species_per_day: bool = True
    order: Literal[
        "first_detection",
        "most_recent",
        "highest_confidence",
        "frequency",
        "alphabetical",
        "random",
    ] = "first_detection"
    display_mode: Literal["clean", "informational"] = "informational"
    image_duration_seconds: int = 20
    retain_daily_slideshows_days: int = 30


class DashboardConfig(BaseModel):
    """§22/§23.3: binds to localhost by default. Setting host to
    0.0.0.0 (or a specific LAN interface address) exposes the
    dashboard to other machines on the network — the "explicit
    configuration" §23.3 asks for before doing that. There is
    deliberately no authentication: this project's design decision
    (2026-08-17) was to accept LAN-wide read-only access with no login,
    matching how most home-network devices behave, rather than build a
    login system for a home LAN dashboard. See README's Phase 3.5
    notes for the full reasoning if that tradeoff ever needs revisiting.
    """

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)


class PhotoFrameConfig(BaseModel):
    manufacturer: str = "Euphro"
    model: str = "WF1561"
    expected_platform: str = "Uhale"
    adapter: str = "local_export"
    target_width: int = 1920
    target_height: int = 1080
    orientation: Literal["landscape", "portrait"] = "landscape"
    preferred_format: Literal["JPEG", "PNG"] = "JPEG"
    export_directory: Path = Path("./data/frame-export/current")


class AppConfig(BaseModel):
    system: SystemConfig = SystemConfig()
    location: LocationConfig
    audio: AudioConfig
    birdnet: BirdNETConfig = BirdNETConfig()
    detections: DetectionsConfig = DetectionsConfig()
    images: ImagesConfig = ImagesConfig()
    dashboard: DashboardConfig = DashboardConfig()
    slideshow: SlideshowConfig = SlideshowConfig()
    photo_frame: PhotoFrameConfig = PhotoFrameConfig()


def load_config(path: Path) -> AppConfig:
    """Load and validate config.yaml. Raises ConfigError on any problem."""
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}. "
            f"Copy config/config.example.yaml to {path} and edit it "
            f"(at minimum: location.latitude/longitude and audio.device_name)."
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path} as YAML: {exc}") from exc

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {path}:\n{exc}") from exc
