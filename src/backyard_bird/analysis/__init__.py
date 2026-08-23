"""BirdNET analysis.

birdnet_adapter.py (Phase 1): one-shot file analysis, the only module
that imports birdnetlib. result_parser.py / deduplicator.py /
worker.py (Phase 3): the queue-driven worker that turns
data/audio/incoming/ into a SQLite detection timeline. clip_extractor.py
(not yet built) will pull the audio clip around each detection out of
its source segment — not required for the Phase 3 exit condition.
"""
