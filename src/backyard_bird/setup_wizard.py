"""Interactive first-run setup: region (location) and microphone
selection, so a new install doesn't require hand-editing config.yaml
to get past the two fields that are otherwise silent failure traps.

Both real bugs this module exists to prevent were hit for real on the
Raspberry Pi host profile (2026-09), not hypothesized:

- A placeholder `audio.device_name` (or a stale one, after a reboot
  renumbers ALSA's hw:N,M index) makes capture retry forever with no
  crash and no obvious error unless you go looking in the logs (§26.1
  working as designed - the *symptom* is just silence).
- A placeholder `location.latitude/longitude` (the Oregon-coast example
  values) doesn't crash anything either - it just quietly filters out
  real local species (§10.3's geographic filter) or admits implausible
  ones, and the only sign is "why are there no detections".

This module holds the two pieces worth unit-testing without a TTY
(geocoding, and writing a value into config.yaml); the interactive
prompt flow itself lives in cli.py's `setup` command, matching the
existing split (doctor.py = testable checks, cli.py = thin wiring).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import re

import requests
import yaml

# Nominatim (OpenStreetMap's free geocoder) needs no API key - the
# right choice for a one-off, low-volume lookup during install rather
# than requiring a new user to go get an API key before they can even
# finish setup. Its usage policy requires a descriptive User-Agent
# identifying the application, same lesson this project already
# learned the hard way with Wikimedia's image API (README's Phase 4
# notes - a missing UA there was a silent 403). Also requires at most
# ~1 request/second, which a single interactive prompt never
# approaches.
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT_HEADER = {
    "User-Agent": "BackyardBirdDiscoverySystem/0.1 (local hobby project; no public deployment)"
}
_REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str


def geocode_location(query: str, *, session: requests.Session | None = None) -> GeocodeResult | None:
    """Resolve a free-form place name (city, state, ZIP/postal code,
    "City, ST", etc.) to coordinates via Nominatim. Returns None if
    nothing matched or the request failed - never raises, so the
    interactive wizard can fall back to asking for lat/long directly
    rather than crashing setup over a network hiccup.
    """
    session = session or requests.Session()
    try:
        response = session.get(
            _NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers=_USER_AGENT_HEADER,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not results:
        return None

    first = results[0]
    try:
        return GeocodeResult(
            latitude=float(first["lat"]),
            longitude=float(first["lon"]),
            display_name=str(first.get("display_name", query)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _yaml_scalar(value: object) -> str:
    """Render `value` as a YAML scalar, quoting it if (and only if)
    YAML would otherwise misparse it - e.g. a device name containing a
    colon, like "TONOR G11 USB microphone: Audio (hw:1,0)". That exact
    string, written unquoted, is the config.yaml bug this project hit
    live on the Pi (README's Linux/Raspberry Pi support notes): a bare
    embedded colon reads as a second YAML mapping key. Reusing PyYAML's
    own dumper here instead of hand-rolling quoting rules is the whole
    point - it can't repeat that mistake.
    """
    dumped = yaml.safe_dump(value, default_flow_style=True).strip()
    if dumped.endswith("..."):
        dumped = dumped[: -len("...")].rstrip()
    return dumped


def set_config_value(config_path: Path, section: str, key: str, value: object) -> bool:
    """Replace a scalar `key: ...` line within a top-level `section:`
    block in config.yaml, leaving every other line - comments,
    formatting, unrelated sections - untouched.

    Deliberately not a full YAML round-trip: that would need a
    comment-preserving parser (e.g. ruamel.yaml) as a new dependency
    just to protect config.example.yaml's field-documentation comments
    (CLAUDE.md: "Document all configuration fields") from being
    silently dropped by a plain load-then-dump. A line-scoped regex
    substitution keeps them intact by construction instead, for a
    handful of well-known fields.

    Returns False (and writes nothing) if `section` or `key` isn't
    found, so a caller can warn rather than silently no-op - e.g. if
    config.yaml's structure ever changes out from under this function.
    """
    lines = config_path.read_text().splitlines(keepends=True)
    section_header = re.compile(rf"^{re.escape(section)}:\s*(#.*)?$")
    key_line = re.compile(rf"^(\s+){re.escape(key)}:(\s.*)?$")
    top_level = re.compile(r"^\S")

    in_section = False
    for i, line in enumerate(lines):
        if section_header.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if top_level.match(line):
            break  # reached the next top-level section without finding the key
        match = key_line.match(line)
        if match:
            indent = match.group(1)
            lines[i] = f"{indent}{key}: {_yaml_scalar(value)}\n"
            config_path.write_text("".join(lines))
            return True
    return False
