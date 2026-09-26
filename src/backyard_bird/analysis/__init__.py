"""BirdNET analysis.

birdnet_adapter.py (Phase 1): one-shot file analysis, the only module
that imports birdnetlib. result_parser.py / deduplicator.py /
worker.py (Phase 3): the queue-driven worker that turns
data/audio/incoming/ into a SQLite detection timeline.

Pulling the audio clip around a detection out of its source segment —
`clip_extractor.py` in §24's suggested layout — ended up in
backyard_bird.audio.clips (extract_clip(), which worker.py calls),
next to the rest of the WAV handling rather than in this package. No
clip_extractor.py is coming.
"""
