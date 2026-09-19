from pathlib import Path

import numpy as np

from backyard_bird.audio.gain import (
    GAIN_MAX,
    GAIN_MIN,
    apply_gain,
    clamp_gain,
    read_gain_control,
    write_gain_control,
)


def test_clamp_gain_passes_through_in_range_values() -> None:
    assert clamp_gain(2.5) == 2.5


def test_clamp_gain_floors_below_minimum() -> None:
    assert clamp_gain(0.0) == GAIN_MIN
    assert clamp_gain(-5.0) == GAIN_MIN


def test_clamp_gain_ceilings_above_maximum() -> None:
    assert clamp_gain(1000.0) == GAIN_MAX


def test_write_then_read_gain_control_round_trips(tmp_path: Path) -> None:
    control_path = tmp_path / "run" / "mic_gain.json"
    applied = write_gain_control(control_path, 3.0)

    assert applied == 3.0
    assert control_path.exists()
    assert read_gain_control(control_path, default=1.0) == 3.0


def test_write_gain_control_clamps_out_of_range_values(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_gain.json"
    applied = write_gain_control(control_path, 999.0)

    assert applied == GAIN_MAX
    assert read_gain_control(control_path, default=1.0) == GAIN_MAX


def test_write_gain_control_overwrites_atomically(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_gain.json"
    write_gain_control(control_path, 2.0)
    write_gain_control(control_path, 4.0)

    assert read_gain_control(control_path, default=1.0) == 4.0
    assert list(control_path.parent.glob("mic_gain*")) == [control_path]  # no stray .tmp left behind


def test_read_gain_control_returns_default_when_file_missing(tmp_path: Path) -> None:
    assert read_gain_control(tmp_path / "does_not_exist.json", default=1.5) == 1.5


def test_read_gain_control_returns_default_on_malformed_json(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_gain.json"
    control_path.write_text("not json at all")

    assert read_gain_control(control_path, default=1.5) == 1.5


def test_read_gain_control_returns_default_when_gain_key_missing(tmp_path: Path) -> None:
    control_path = tmp_path / "mic_gain.json"
    control_path.write_text('{"something_else": 1}')

    assert read_gain_control(control_path, default=1.5) == 1.5


def test_apply_gain_is_a_noop_at_1x() -> None:
    chunk = np.array([100, -100, 32000], dtype=np.int16)
    result = apply_gain(chunk, 1.0)
    assert np.array_equal(result, chunk)


def test_apply_gain_scales_samples() -> None:
    chunk = np.array([100, -100, 1000], dtype=np.int16)
    result = apply_gain(chunk, 2.0)
    assert list(result) == [200, -200, 2000]
    assert result.dtype == np.int16


def test_apply_gain_clips_instead_of_wrapping_around() -> None:
    # Without clipping, boosting a near-full-scale sample would
    # overflow int16's two's-complement range and wrap to a bogus,
    # oppositely-signed value — far worse-sounding than clipping.
    chunk = np.array([30000, -30000], dtype=np.int16)
    result = apply_gain(chunk, 3.0)
    assert list(result) == [32767, -32768]


def test_apply_gain_handles_silence() -> None:
    chunk = np.zeros(10, dtype=np.int16)
    result = apply_gain(chunk, 5.0)
    assert np.array_equal(result, chunk)
