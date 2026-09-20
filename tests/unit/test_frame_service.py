from pathlib import Path

from backyard_bird.config import PhotoFrameConfig
from backyard_bird.frame.adapters.local_export import LocalExportAdapter
from backyard_bird.frame.adapters.unconfigured import UnconfiguredFrameAdapter
from backyard_bird.frame.service import build_frame_adapter


def test_build_frame_adapter_returns_local_export_for_local_export(tmp_path: Path) -> None:
    config = PhotoFrameConfig(adapter="local_export", export_directory=tmp_path / "current")
    adapter = build_frame_adapter(config)
    assert isinstance(adapter, LocalExportAdapter)


def test_build_frame_adapter_falls_back_to_unconfigured_for_unknown_adapter_name() -> None:
    config = PhotoFrameConfig(adapter="uhale_web")  # not implemented yet
    adapter = build_frame_adapter(config)
    assert isinstance(adapter, UnconfiguredFrameAdapter)
    assert adapter.requested_adapter_name == "uhale_web"
