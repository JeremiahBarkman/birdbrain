"""Exercises the real BirdNET model against a known sample WAV.

This is the automated form of the Phase 1 exit condition (§29):
"A supplied WAV file produces parsed BirdNET results through the
project adapter." It's marked `integration` because loading the model
is slow (several seconds) and it needs a real sample file present —
see tests/sample_audio/README.md.
"""
from pathlib import Path

import pytest

from backyard_bird.config import BirdNETConfig, LocationConfig
from backyard_bird.analysis.birdnet_adapter import AnalysisResult, analyze_file

SAMPLE_AUDIO_DIR = Path(__file__).resolve().parents[1] / "sample_audio"
SAMPLE_WAV_CANDIDATES = sorted(SAMPLE_AUDIO_DIR.glob("*.wav"))

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not SAMPLE_WAV_CANDIDATES,
    reason=f"no sample WAV in {SAMPLE_AUDIO_DIR} (see tests/sample_audio/README.md)",
)
def test_analyze_file_returns_parsed_detections() -> None:
    sample_wav = SAMPLE_WAV_CANDIDATES[0]
    birdnet_config = BirdNETConfig()
    location = LocationConfig(latitude=45.0, longitude=-123.0)

    result = analyze_file(sample_wav, birdnet_config, location=location)

    assert isinstance(result, AnalysisResult)
    assert result.analysis_duration_seconds > 0
    for detection in result.detections:
        assert detection.scientific_name
        assert detection.common_name
        assert 0.0 <= detection.confidence <= 1.0
        assert detection.end_time_seconds >= detection.start_time_seconds
