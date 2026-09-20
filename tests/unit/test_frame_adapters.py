import json
from datetime import datetime, timezone
from pathlib import Path

from backyard_bird.config import PhotoFrameConfig
from backyard_bird.frame.adapters.local_export import LocalExportAdapter
from backyard_bird.frame.adapters.unconfigured import UnconfiguredFrameAdapter
from backyard_bird.frame.manifest import SlideshowManifest, SlideshowManifestItem


def _manifest(items: list[SlideshowManifestItem]) -> SlideshowManifest:
    return SlideshowManifest(local_date="2026-09-20", generated_at_utc=datetime.now(timezone.utc), items=items)


def _item(name: str, order: int = 0) -> SlideshowManifestItem:
    return SlideshowManifestItem(
        scientific_name=f"Testus {name}",
        common_name=name,
        rendered_file_path=Path(f"{name}.jpg"),
        display_order=order,
        detection_count=1,
        first_detected_at_utc="2026-09-20T06:00:00+00:00",
    )


def _write_fake_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff fake jpeg bytes for testing")


# -- UnconfiguredFrameAdapter -------------------------------------------------


def test_unconfigured_adapter_reports_not_configured_when_no_adapter_named() -> None:
    adapter = UnconfiguredFrameAdapter()
    assert adapter.test_connection().ok is False
    assert adapter.get_status().configured is False
    assert "No photo_frame.adapter configured" in adapter.get_status().detail


def test_unconfigured_adapter_names_the_unrecognized_adapter() -> None:
    adapter = UnconfiguredFrameAdapter(requested_adapter_name="uhale_web")
    assert "uhale_web" in adapter.test_connection().message
    assert "uhale_web" in adapter.get_status().detail


def test_unconfigured_adapter_publish_and_cleanup_report_failure_not_raise(tmp_path: Path) -> None:
    adapter = UnconfiguredFrameAdapter()
    publish = adapter.publish_slideshow(tmp_path, _manifest([]))
    assert publish.ok is False
    assert publish.files_delivered == 0

    cleanup = adapter.remove_old_slideshows(retention_days=30)
    assert cleanup.ok is False


# -- LocalExportAdapter -------------------------------------------------------


def test_local_export_test_connection_succeeds_when_writable(tmp_path: Path) -> None:
    config = PhotoFrameConfig(export_directory=tmp_path / "frame-export" / "current")
    adapter = LocalExportAdapter(config)

    result = adapter.test_connection()

    assert result.ok is True


def test_local_export_publish_copies_slides_and_writes_manifest_and_readme(tmp_path: Path) -> None:
    slideshow_dir = tmp_path / "rendered"
    _write_fake_jpeg(slideshow_dir / "robin.jpg")
    _write_fake_jpeg(slideshow_dir / "jay.jpg")
    export_dir = tmp_path / "frame-export" / "current"
    adapter = LocalExportAdapter(PhotoFrameConfig(export_directory=export_dir))

    result = adapter.publish_slideshow(slideshow_dir, _manifest([_item("robin"), _item("jay")]))

    assert result.ok is True
    assert result.files_attempted == 2
    assert result.files_delivered == 2
    assert (export_dir / "robin.jpg").read_bytes() == (slideshow_dir / "robin.jpg").read_bytes()
    assert (export_dir / "jay.jpg").exists()
    assert (export_dir / "README.txt").exists()
    manifest_data = json.loads((export_dir / "manifest.json").read_text())
    assert manifest_data["species_count"] == 2


def test_local_export_publish_skips_missing_slides_without_failing_the_rest(tmp_path: Path) -> None:
    slideshow_dir = tmp_path / "rendered"
    _write_fake_jpeg(slideshow_dir / "robin.jpg")  # "jay.jpg" deliberately never written
    export_dir = tmp_path / "frame-export" / "current"
    adapter = LocalExportAdapter(PhotoFrameConfig(export_directory=export_dir))

    result = adapter.publish_slideshow(slideshow_dir, _manifest([_item("robin"), _item("jay")]))

    assert result.ok is False  # not every attempted slide was delivered
    assert result.files_attempted == 2
    assert result.files_delivered == 1
    assert (export_dir / "robin.jpg").exists()
    assert not (export_dir / "jay.jpg").exists()


def test_local_export_publish_replaces_previous_export_in_place(tmp_path: Path) -> None:
    slideshow_dir = tmp_path / "rendered"
    _write_fake_jpeg(slideshow_dir / "robin.jpg")
    export_dir = tmp_path / "frame-export" / "current"
    adapter = LocalExportAdapter(PhotoFrameConfig(export_directory=export_dir))

    adapter.publish_slideshow(slideshow_dir, _manifest([_item("robin")]))
    assert (export_dir / "robin.jpg").exists()

    # A second day's slideshow no longer includes yesterday's species —
    # the old file must not survive the swap.
    _write_fake_jpeg(slideshow_dir / "jay.jpg")
    adapter.publish_slideshow(slideshow_dir, _manifest([_item("jay")]))

    assert not (export_dir / "robin.jpg").exists()
    assert (export_dir / "jay.jpg").exists()
    # No leftover .building/.previous scratch directories after a clean run.
    assert not export_dir.with_name(export_dir.name + ".building").exists()
    assert not export_dir.with_name(export_dir.name + ".previous").exists()


def test_local_export_publish_with_empty_manifest_succeeds_trivially(tmp_path: Path) -> None:
    export_dir = tmp_path / "frame-export" / "current"
    adapter = LocalExportAdapter(PhotoFrameConfig(export_directory=export_dir))

    result = adapter.publish_slideshow(tmp_path / "rendered", _manifest([]))

    assert result.ok is True
    assert result.files_attempted == 0
    assert (export_dir / "manifest.json").exists()


def test_local_export_remove_old_slideshows_is_a_reported_noop(tmp_path: Path) -> None:
    adapter = LocalExportAdapter(PhotoFrameConfig(export_directory=tmp_path / "current"))
    result = adapter.remove_old_slideshows(retention_days=30)
    assert result.ok is True
    assert result.removed_count == 0


def test_local_export_status_before_any_publish(tmp_path: Path) -> None:
    adapter = LocalExportAdapter(PhotoFrameConfig(export_directory=tmp_path / "current"))
    status = adapter.get_status()
    assert status.configured is True
    assert "No export yet" in status.detail


def test_local_export_status_after_publish_reports_manifest_summary(tmp_path: Path) -> None:
    slideshow_dir = tmp_path / "rendered"
    _write_fake_jpeg(slideshow_dir / "robin.jpg")
    export_dir = tmp_path / "current"
    adapter = LocalExportAdapter(PhotoFrameConfig(export_directory=export_dir))
    adapter.publish_slideshow(slideshow_dir, _manifest([_item("robin")]))

    status = adapter.get_status()

    assert status.configured is True
    assert "1 slide" in status.detail
    assert "2026-09-20" in status.detail
