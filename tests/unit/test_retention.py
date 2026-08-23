import os
import time
from pathlib import Path

from backyard_bird.audio.retention import sweep_incoming


def test_sweep_deletes_only_old_files(tmp_path: Path) -> None:
    old_file = tmp_path / "old.wav"
    new_file = tmp_path / "new.wav"
    old_file.write_bytes(b"x")
    new_file.write_bytes(b"x")

    old_time = time.time() - 10 * 86400  # 10 days old
    os.utime(old_file, (old_time, old_time))

    deleted = sweep_incoming(tmp_path, retention_days=7)

    assert deleted == [old_file]
    assert not old_file.exists()
    assert new_file.exists()


def test_sweep_noop_when_retention_non_positive(tmp_path: Path) -> None:
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    assert sweep_incoming(tmp_path, retention_days=0) == []
    assert f.exists()


def test_sweep_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert sweep_incoming(tmp_path / "does-not-exist", retention_days=7) == []
