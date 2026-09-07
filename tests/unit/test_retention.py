import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backyard_bird.audio import retention
from backyard_bird.audio.retention import enforce_disk_space_floor, sweep_failed, sweep_incoming, sweep_processed

# processed/, failed/, and incoming/ all delete the same way (§8.1/§9)
# — one parametrized set of behavioral tests covers all three sweeps,
# rather than duplicating each case per function.
SWEEP_FUNCTIONS = [sweep_incoming, sweep_processed, sweep_failed]


@pytest.mark.parametrize("sweep", SWEEP_FUNCTIONS)
def test_sweep_deletes_only_old_files(tmp_path: Path, sweep) -> None:
    old_file = tmp_path / "old.wav"
    new_file = tmp_path / "new.wav"
    old_file.write_bytes(b"x")
    new_file.write_bytes(b"x")

    old_time = time.time() - 10 * 86400  # 10 days old
    os.utime(old_file, (old_time, old_time))

    deleted = sweep(tmp_path, retention_days=7)

    assert deleted == [old_file]
    assert not old_file.exists()
    assert new_file.exists()


@pytest.mark.parametrize("sweep", SWEEP_FUNCTIONS)
def test_sweep_noop_when_retention_non_positive(tmp_path: Path, sweep) -> None:
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    assert sweep(tmp_path, retention_days=0) == []
    assert f.exists()


@pytest.mark.parametrize("sweep", SWEEP_FUNCTIONS)
def test_sweep_missing_dir_returns_empty(tmp_path: Path, sweep) -> None:
    assert sweep(tmp_path / "does-not-exist", retention_days=7) == []


# -- enforce_disk_space_floor ---------------------------------------------
#
# A hard backstop behind the age-based sweeps above: fakes
# shutil.disk_usage as capacity-minus-bytes-still-on-disk, so deleting a
# file genuinely moves free space, the same way it would on a real
# filesystem — without needing gigabyte-scale fixtures.


def _fake_disk(monkeypatch: pytest.MonkeyPatch, tracked_dirs: list[Path], capacity_bytes: int) -> None:
    def fake_disk_usage(_path: Path) -> SimpleNamespace:
        used = sum(p.stat().st_size for d in tracked_dirs if d.exists() for p in d.glob("*.wav"))
        return SimpleNamespace(total=capacity_bytes, used=used, free=capacity_bytes - used)

    monkeypatch.setattr(retention.shutil, "disk_usage", fake_disk_usage)


def _write_aged(path: Path, size_bytes: int, age_seconds: float) -> None:
    path.write_bytes(b"x" * size_bytes)
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))


def test_disk_floor_noop_when_already_above_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "a.wav"
    _write_aged(f, 1000, age_seconds=0)
    _fake_disk(monkeypatch, [tmp_path], capacity_bytes=10_000)  # 9000 bytes free — plenty

    deleted = enforce_disk_space_floor(tmp_path, [tmp_path], min_free_gb=1000 / 1024**3)

    assert deleted == []
    assert f.exists()


def test_disk_floor_deletes_oldest_first_until_floor_met(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old.wav"
    mid = tmp_path / "mid.wav"
    new = tmp_path / "new.wav"
    _write_aged(old, 1000, age_seconds=300)
    _write_aged(mid, 1000, age_seconds=200)
    _write_aged(new, 1000, age_seconds=100)
    _fake_disk(monkeypatch, [tmp_path], capacity_bytes=3500)  # 500 bytes free with all 3 present

    deleted = enforce_disk_space_floor(tmp_path, [tmp_path], min_free_gb=1200 / 1024**3)

    # Only enough of the oldest files to clear the floor: deleting
    # "old" alone takes free from 500 to 1500 bytes, which already
    # clears 1200 — "mid" and "new" are left alone.
    assert deleted == [old]
    assert not old.exists()
    assert mid.exists() and new.exists()


def test_disk_floor_drains_directories_in_priority_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    processed_dir.mkdir()
    failed_dir.mkdir()
    processed_file = processed_dir / "old.wav"
    failed_file = failed_dir / "old.wav"
    _write_aged(processed_file, 1000, age_seconds=100)
    _write_aged(failed_file, 1000, age_seconds=100)
    _fake_disk(monkeypatch, [processed_dir, failed_dir], capacity_bytes=2100)  # 100 bytes free

    # Deleting processed_file alone (1000 bytes) only gets free to 1100
    # — still short of the 1900 floor — so failed_file must go too.
    # processed/ is still drained first, before failed/ is touched.
    deleted = enforce_disk_space_floor(processed_dir, [processed_dir, failed_dir], min_free_gb=1900 / 1024**3)

    assert deleted == [processed_file, failed_file]


def test_disk_floor_disabled_when_min_free_gb_non_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "a.wav"
    _write_aged(f, 1000, age_seconds=0)
    _fake_disk(monkeypatch, [tmp_path], capacity_bytes=1000)  # 0 bytes free

    assert enforce_disk_space_floor(tmp_path, [tmp_path], min_free_gb=0) == []
    assert f.exists()
