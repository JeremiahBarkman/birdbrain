# Sample audio

Put a known-good bird-call WAV here (e.g. `soundscape.wav`) to exercise
`tests/integration/test_birdnet_adapter.py` and to smoke-test the CLI:

```bash
bird-display analyze file tests/sample_audio/soundscape.wav
```

Files placed here are not committed to git (`data/`-style content, kept
local) — this directory only holds this README so it exists in the repo.

A convenient source is BirdNET-Analyzer's own example soundscape,
distributed with the kahst/BirdNET-Analyzer project. Anything with
clear, identifiable bird calls will do.
