"""Picks the configured PhotoFrameAdapter (§17.4) from
photo_frame.adapter — the same registry-lookup shape cli.py's
_build_image_providers() already uses for images.preferred_sources.
"""
from __future__ import annotations

from backyard_bird.config import PhotoFrameConfig
from backyard_bird.frame.adapters.local_export import LocalExportAdapter
from backyard_bird.frame.adapters.unconfigured import UnconfiguredFrameAdapter
from backyard_bird.frame.base import PhotoFrameAdapter

_ADAPTERS = {
    "local_export": LocalExportAdapter,
}


def build_frame_adapter(config: PhotoFrameConfig) -> PhotoFrameAdapter:
    adapter_cls = _ADAPTERS.get(config.adapter)
    if adapter_cls is None:
        return UnconfiguredFrameAdapter(requested_adapter_name=config.adapter)
    return adapter_cls(config)
