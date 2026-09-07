import os
import time
from pathlib import Path

import pytest

from backyard_bird.audio.retention import sweep_failed, sweep_incoming, sweep_processed

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
