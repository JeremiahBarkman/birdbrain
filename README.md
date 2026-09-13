# Backyard Bird Discovery System

Local-first pipeline: outdoor microphone → BirdNET-Analyzer → SQLite
detection timeline → daily slideshow → Euphro WF1561 photo frame.

Full requirements and architecture: [`Backyard_Bird_Discovery_System_Requirements.md`](Backyard_Bird_Discovery_System_Requirements.md).
Development rules: [`CLAUDE.md`](CLAUDE.md).

## Status

**Phase 1 — Environment and BirdNET Validation**: done (see requirements §29).
**Phase 2 — Continuous Audio Capture**: code done, live 24h soak test pending (below).
**Phase 3 — Detection Pipeline and Database**: done, validated end-to-end with a real recording (below).
**Phase 3.5 — Live Stats Dashboard**: done (below). Not one of §29's numbered
phases — inserted ahead of Phase 4 at the user's request, implementing
§22's "Local Status Interface" early so Phase 4's bird images have a
page to appear on.
**Phase 4 — Image Acquisition**: done — confirmed live end-to-end
(House Finch photo cached and served by the dashboard), two providers
(Wikimedia + iNaturalist, config-driven), four real bugs found and
fixed along the way — see below.
**Linux/Raspberry Pi support**: added 2026-09-13 (below), not a §29
phase — install/doctor/dependency changes needed to run this codebase
on Ubuntu 24.04 (aarch64) as a second host profile alongside the Mac
mini, ahead of moving the deployment to a Raspberry Pi 4B (4GB). Not
yet installed or run on the physical Pi — see "Not yet validated"
below.

Implemented so far:

- Repository skeleton and Python packaging
- Config loader (`config/config.yaml`, validated with pydantic)
- Microphone discovery and one-shot test recording
- BirdNET adapter for one-shot file analysis
- Continuous, overlap-segmented, restart-safe capture into
  `data/audio/incoming/` (`bird-display capture run`), with automatic
  device-drop reconnection and raw-audio retention
- SQLite schema + migrations, and a queue-driven analyzer
  (`bird-display analyze run`) that turns captured segments into a
  searchable detection timeline, with duplicate suppression across
  overlapping segments and full crash recovery
- Timeline CLI: `queue status`, `database migrate`/`integrity-check`,
  `detections list`/`today`, `species list`
- Live status dashboard (`bird-display dashboard run`, §22): a small
  Flask app at `http://127.0.0.1:8765` showing all-time and today's
  species/detection counts, the most recent detection, and a
  per-species table (count, first/last seen, time seen), polling
  `/api/stats` every 5s
- Image acquisition (§15): `bird-display images fetch-missing`/`refresh`
  search Wikimedia Commons and iNaturalist (config-driven via
  `images.preferred_sources`), validate and rank candidates, cache the
  winner, and record an approved/unavailable outcome per species — with
  the dashboard now actually serving the cached images it reserved a
  slot for in Phase 3.5

Not yet built (later phases): the slideshow builder, daily species
aggregation, and the photo-frame adapter.

### Phase 2 implementation (2026-08-16)

`audio/segmenter.py` is pure buffering/overlap math (no device
dependency) — unit-tested directly. `audio/capture_service.py` wraps
it with a `sounddevice.InputStream`, a background-safe callback that
only ever copies chunks into a queue (so a slow disk can't glitch the
realtime audio thread), device-drop detection with backoff reconnect,
hourly retention sweeps, and atomic `.partial` → rename writes per
§8.4. `audio/retention.py` caps `data/audio/incoming/` growth per
`audio.raw_audio_retention_days`. All three are unit-tested with a
faked `sounddevice.InputStream` and faked device lookup — no real
mic/TCC involved, so these tests run the same over SSH as locally.

**What's not validated yet, and can't be from here:** actually running
`bird-display capture run` needs live microphone access, which hits
the same SSH/TCC wall as the earlier mic test — this has to be run at
the physical console (or Screen Sharing), not over this session. Two
separate things are worth checking once you can:

1. A short smoke test — `bird-display capture run --max-segments 4`
   (~90s with default settings) — to confirm real segments land in
   `data/audio/incoming/` with correct overlap and no leftover
   `.partial` files.
2. The actual Phase 2 exit condition: 24 hours of continuous capture.
   Nothing currently keeps the process running across a reboot or
   terminal close — that's `launchd` wiring, explicitly a Phase 8
   deliverable, not Phase 2. For now, a 24h run means leaving
   `bird-display capture run` running in a terminal (or `nohup`'d) for
   a full day and checking segment continuity and disk growth
   afterward.

### Phase 3 implementation and validation (2026-08-16)

Scoped to the Phase 3 deliverables only: `migrations/001_initial_schema.sql`
covers `audio_segments`/`species`/`detections` (§12.2-§12.4) —
`daily_species_summary`, `bird_images`, `slideshows`/`slideshow_items`,
`frame_delivery_attempts`, and `service_health` are later phases' own
migrations when those phases start. `database/repositories.py` is the
only module with raw SQL; write functions there don't commit
themselves, so callers (`analysis/worker.py`) control the transaction
boundary — this is what makes crash recovery correct (see
`recover_stuck_segments`'s docstring for the four crash windows it
handles without ever double-inserting detections).

Duplicate suppression compares absolute detection timestamps (segment
start + BirdNET's in-file offset) across same-species/same-mic
detections within `duplicate_window_seconds` — catches the same call
being independently detected in two segments' overlapping region
(§8.2's 3s overlap vs. the default 5s window).

**Validated for real**, not just unit-tested: the actual House Finch
recording from the mic-permission test was fed through the complete
pipeline — `database migrate` → dropped into
`data/audio/incoming/` → `bird-display analyze run --max-files 1` →
`detections list`/`species list`. It correctly reappeared as `0.50
House Finch (Haemorhous mexicanus)`, moved to `data/audio/processed/`,
and `database integrity-check` passed. Crash recovery was also checked
live, not just in tests: a file dropped directly into `processing/`
(simulating a worker that died mid-claim, with no DB row yet) was
correctly requeued and processed on the next `analyze run`, landing as
a second, distinct detection rather than being lost or double-counted.

One known gap, documented rather than silently glossed over:
`birdnet.retain_raw_results` isn't fully wired yet. BirdNET is called
with `min_conf` set to `database_minimum_confidence`, so sub-threshold
candidates are filtered out by BirdNET itself before this codebase
ever sees them — "retain low-confidence candidates for diagnostics"
(§10.1) would need the adapter to always call with `min_conf=0` and do
that filtering here instead. Not required for the Phase 3 exit
condition (a reliable, searchable timeline of *qualifying* detections),
but worth knowing before relying on that config field.

### Phase 3.5 implementation and validation (2026-08-16)

`web/app.py` is a small Flask factory; `web/routes.py` is the only
module that touches HTTP — it opens a fresh, short-lived SQLite
connection per request rather than sharing one across requests (sqlite3
connections aren't safe to share across threads, and Flask's dev
server may serve requests on different threads). Two new read-only
queries in `repositories.py` (`get_overall_stats`) back the summary
cards; the per-species table reuses `list_species_summary` from Phase 3
unchanged.

Real-time is plain `fetch()` + `setInterval` polling every 5s, not
WebSockets/SSE — deliberately: detections only arrive on a ~30s cadence
(one per audio segment), so a few-second poll is indistinguishable from
true push-based real-time here, for far less moving infrastructure and
zero new dependencies beyond Flask itself.

Each species row and the "most recent detection" card have an
`image_url` field, currently always `null`, with a placeholder icon
shown in its place — reserved specifically so Phase 4 only has to
populate the field once `bird_images` exists, not restructure the page.

**Validated two ways:** `pytest` covers the routes via Flask's test
client (empty DB, seeded DB, malformed-timestamp handling) with no
real socket involved; separately, the actual server was started for
real (`bird-display dashboard run`) against the live database from the
Phase 3 House Finch test and queried with `curl` — `/api/stats`
correctly reported 2 detections, 1 species, `duration_seen_seconds:
540.0` (9 minutes between the two real detections), and `/`,
`/static/dashboard.js`, `/static/style.css` all returned 200. This
needed no live microphone, so — unlike capture/mic testing — it could
be run and confirmed directly over this SSH session.

**Not verified: how it actually looks.** curl confirms the server
works — correct HTTP responses, correct JSON — but says nothing about
whether the page reads well as a page. Run `bird-display dashboard run`
and open `http://127.0.0.1:8765/` in a real browser to check.

### Phase 4 implementation and validation (2026-08-16)

`images/providers/base.py` defines `ImageProvider`/`ImageCandidate` —
the seam CLAUDE.md rule 8 requires. Two adapters:
`images/providers/wikimedia.py` and, added once Wikimedia turned out to
be throttled (below), `images/providers/inaturalist.py` — its two-step
lookup (search by name to find the taxon, then fetch that taxon's full
photo list) was worked out against the live API before writing any
code, same as Wikimedia's. Which providers actually run, and in what
order, is driven by `images.preferred_sources` in config.yaml, not
hardcoded. `images/validation.py` and `images/ranking.py` are pure and
independently tested.
`images/cache.py` owns `data/images/species/<scientific-name-slug>/`
and the cover-fit crop to 1920×1080 (§16.4) done at acquisition time.
`images/service.py` wires it together: search (§15.2's query order,
minus the region-based forms — no region name is configured anywhere
yet, only lat/long, so those two are honestly skipped rather than
faked) → filter on license metadata if required → rank → download →
validate → cache → record 'approved' or 'unavailable' in the new
`bird_images` table (migration 002). `database migrate` picks it up
automatically (idempotent, like migration 001).

The dashboard's `image_url` field — reserved as always-`null` back in
Phase 3.5 — is now real: `routes.py` looks up each species' approved
image by scientific name and serves it from the cache via a new
`/media/species/<slug>/<filename>` route.

**Two real bugs found and fixed by actually running this against the
live Wikimedia API** (not just the mocked unit tests):

1. **HTTP 403 on every download.** The *search* API calls carried a
   descriptive User-Agent (Wikimedia requires one), but the separate
   image *download* request didn't — rejected as a generic
   `python-requests` bot. Fixed by adding the same header to downloads.
2. **HTTP 429 immediately after**, with Wikimedia's own error message
   naming the cause and the fix: direct `upload.wikimedia.org` original-file
   requests are rate-limited/blocked for automated clients, and their
   response pointed at the thumbnail renderer instead. Fixed by
   requesting `iiurlwidth=1920` and using the returned `thumburl` — which
   is also strictly better for us: smaller downloads, and we only need
   frame-resolution images anyway.
3. **A second 429 anyway**, this time "does not comply with our robot
   policy" — caused by this codebase's own behavior, not a missing
   header: trying up to 20 ranked candidates back-to-back with zero
   delay is exactly the "disruptive" pattern their error message asked
   to avoid. Fixed by capping download attempts at 5 per species and
   adding a 1s pause between them.

**Wikimedia's throttle didn't clear quickly** — a follow-up attempt
with the fully-fixed, well-behaved client (5 attempts, 1s apart) still
got `429` on every one, same error code as before, ~17 minutes later.
Rather than keep polling their servers hoping it clears, iNaturalist
was added as a second provider instead (§15.3 already named it as an
initial candidate; §3.2 wants the system "independent of a single
image provider" anyway — this was going to be worth doing regardless).

That surfaced a third real bug, this time in the ranking/selection
logic itself, not either API: with both providers configured, all 5
download attempts still went to Wikimedia and still all failed — one
provider returning many decent-scoring candidates was crowding the
other out of the download budget entirely before it ever got a turn.
Fixed by interleaving the ranked list across providers
(`_interleave_by_provider` in `service.py`) before truncating to the
attempt cap, so every configured provider is guaranteed an early try
regardless of how many candidates any other one contributes.

**A real image is now confirmed working end to end**, live: Wikimedia
attempt #1 hit the same throttle as always, attempt #2 — now correctly
an iNaturalist candidate — succeeded immediately. House Finch,
2048×1436, CC BY-NC-ND, photographer Leonardo Guzmán, cached to
`data/images/species/haemorhous-mexicanus/`, and confirmed served
correctly by the dashboard at
`/media/species/haemorhous-mexicanus/optimized_1920x1080.jpg`.

That last step surfaced a **fourth bug**, and the least obvious one:
the served image 404'd even though the file definitely existed on
disk. Cause: Flask's `send_from_directory` resolves a relative
directory against the *app's own module path*
(`src/backyard_bird/web/`), not the process's working directory — and
`data_directory` in config.yaml is `./data`, relative, same as every
other path in this project. `app.py` now calls `.resolve()` on it
before storing it in Flask's config. This is exactly the kind of bug
that no amount of testing with `tmp_path` (always absolute) would ever
catch — `tests/unit/test_web.py` now has a dedicated regression test
that `monkeypatch.chdir()`s and uses a genuinely relative path, the
same shape real usage takes.

### Automatic image search on new detections (2026-08-17)

Phase 4 as originally built only searched for images on manual command
(`images fetch-missing`/`refresh`) — a real gap against §15.1, which
says a search should happen automatically "when a newly detected
species has no approved cached image." Found the gap firsthand: a
real Western Screech-Owl got detected (14 times, via the desktop
launcher actually running end-to-end for the first time) and its photo
never appeared.

Fixed with `bird-display images watch` (`images/service.py`'s
`run_watch_loop`) — a fourth long-running service, polling every 10
minutes for species needing a photo and fetching automatically.
Deliberately its own separate process rather than a hook inside the
analyzer: an image search is slow and network-dependent in a way audio
analysis must never be (CLAUDE.md rule 12), so it polls the same
database the analyzer only ever writes to, entirely decoupled. Wired
into `start_all.sh`/`stop_all.sh` as a third managed service alongside
capture and analyze.

Along the way, `list_species_needing_image_search` also stopped
treating an 'unavailable' verdict as permanent — it now expires after
the same `refresh_after_days` window an approved image's rotation
uses, since (as this project's own Wikimedia throttle demonstrated) an
'unavailable' record can just as easily reflect a transient provider
problem as a genuine no-image-exists case.

Confirmed live: started `images watch` alongside the already-running
capture/analyzer/dashboard (started independently, by actually
double-clicking the desktop launcher — not by me) without disturbing
them, and it correctly found nothing to do on its first cycle, since
both real species detected so far (House Finch, Western Screech-Owl)
already had approved images from the manual fetch run moments earlier.

### LAN access and a deliberate no-auth decision (2026-08-17)

The dashboard binds to `127.0.0.1` (localhost-only) by default, per
§23.3. A new `dashboard:` config section (`host`, `port`) makes
opening it to the LAN an explicit config change rather than a code
change — set `host: 0.0.0.0` in `config.yaml` and it's reachable from
any device on the network; `bird-display dashboard run` picks this up
automatically with no flags needed, so the desktop launcher didn't
need to change at all. `--host`/`--port` CLI flags still override
config when passed directly.

§23.3 actually says remote access "shall require explicit
configuration **and authentication**." There is deliberately no
authentication here — asked, and the answer was explicit: this is a
prototype, and auth is deferred rather than skipped for good. Not a
permanent architectural stance, just not built yet. Recorded here per
CLAUDE.md ("do not silently deviate from the requirements... explain
the conflict") since it's a real, intentional gap against the literal
spec text — **add authentication before this moves past prototype
status**, especially if the page ever holds anything more sensitive
than read-only detection counts, or if the network it's on stops being
fully trusted (a guest WiFi network sharing the same LAN, for
instance).

Confirmed live: restarted the dashboard with no explicit `--host`,
confirmed it picked up `0.0.0.0` from config.yaml and correctly
reported the machine's actual LAN address
(`http://10.0.0.218:8765` on this network) rather than the
unusable literal `0.0.0.0`, and confirmed the page loads via both
`127.0.0.1` and that LAN address.

### Phase 1 validation (2026-08-16, this Mac mini)

- `birdnetlib` does **not** declare `librosa` or a TFLite backend as
  dependencies even though it hard-requires both at import time.
  `tflite-runtime` has no official macOS arm64 wheels, so `tensorflow`
  (pinned `>=2.13,<2.17` for Python 3.9 compatibility) supplies the
  interpreter instead — both are now explicit `pyproject.toml` deps.
  Model weights ship inside the `birdnetlib` wheel itself, so
  inference needs no network access.
- `bird-display analyze file tests/sample_audio/soundscape.wav`
  (standard BirdNET-Analyzer example clip, 120s/48kHz/mono) correctly
  identified 4 detections (Black-capped Chickadee, Dark-eyed Junco,
  House Finch ×2) with geographic filtering narrowing the model to 267
  candidate species for the example lat/long.
- Performance on this M1/16GB host: model load ~0.05s (cached after
  first call); analysis ~5.4s wall / 7.7s CPU for a 120s clip once
  warm (~22x real-time); peak RSS ~665 MB. Comfortably within budget
  for the 30s segments Phase 2 will produce.
- `bird-display audio list-devices` correctly reported no input
  devices before a microphone was attached (confirmed independently
  via `system_profiler SPAudioDataType`).

### Microphone (2026-08-16)

A **TONOR G11 USB microphone** (USB, mono, 48 kHz, C-Media Electronics)
is now connected and configured as `audio.device_name` in
`config/config.yaml`. `bird-display audio test --seconds 10` recorded
a clip with the right container format (16-bit PCM mono 48kHz,
matching config) and it ran through `bird-display analyze file`
end-to-end without error — but see the correction below: the "0
detections" from that first test was not a valid signal.

**Correction:** both that indoor test and a later 30s outdoor test
(with audible birds present) came back as *silent* recordings — every
sample was exactly `0`, confirmed by checking peak/RMS amplitude and
by reproducing it with a raw `sounddevice.rec()` call outside our
wrapper. Root cause: this session runs over SSH
(`sshd-session → claude → zsh`), with no attached WindowServer/GUI
session, and `TCC.db` shows **zero** microphone grants have ever been
issued on this host. macOS's CoreAudio opens the device and returns
buffers without raising an exception even when the privacy prompt was
never shown/answered — it just delivers silence. The earlier "0
detections — no bird calls" conclusion was wrong; both recordings were
uninformative, not evidence of anything about the audio content.

**Operational constraint this exposes for Phase 2/8:** granting
Terminal.app microphone access locally did *not* fix this — re-running
the same command through this same SSH-attached shell was still
silent. macOS blocks camera/microphone capture entirely for processes
with no attached WindowServer/Aqua session (SSH, in particular),
independent of what's been granted to any app; the TCC check and
system log show no record of the request even being attempted for
this process tree. Confirmed real capture requires running the
command from an actual local GUI context (Terminal.app run directly
at the console, or a Screen Sharing session).

This matters directly for §5.1/§29 Phase 8: the `bird_capture` service
must ship as a `launchd` **LaunchAgent** bound to the user's logged-in
GUI session — not a **LaunchDaemon** (system-level, no GUI session
attached), which would hit this exact same wall permanently. This
doesn't change the architecture (`launchd`-managed Python is still
correct), but it does pin down *which* `launchd` job type is required,
and it means microphone authorization is a manual, physical/console,
one-time setup step — not something scriptable or grantable over SSH.
Worth calling out explicitly in the Phase 8 `doctor` command and
install docs.

**This entire constraint is macOS-specific (§31.1).** On the Linux/Pi
host profile added 2026-09-13, ALSA/PortAudio has no equivalent
GUI-session gate — a user in the `audio` group can open the microphone
over a plain SSH session, console or not. Everything above (TCC,
LaunchAgent-vs-LaunchDaemon, physical-console requirement) applies to
the Mac mini profile only.

Outdoor placement, weatherproofing, wind protection, and cable routing
(§31.1) remain open regardless.

### First real detection (2026-08-16)

Once you granted Terminal.app mic access locally and ran the test from
the physical console, a 30s outdoor recording came back with real
signal (peak -9.8 dBFS, not silent) — but `analyze file` still reported
zero detections at the 0.60 default threshold, despite audible birds.

Diagnosis: re-analyzing the same file with the threshold dropped to
0.10 and geographic filtering off showed BirdNET *was* picking up a
real candidate — House Finch at 0.497 — just under the 0.60 bar, along
with weaker, geographically implausible candidates (a European/Asian
rail, a South American finch) that a correct location would exclude.
`location` in `config.yaml` was still the `config.example.yaml`
placeholder (Oregon coast, 45.0/-123.0) rather than the real
installation site.

Fixed both:
- `location` set to Carlton, OR (45.2853, -123.1998). Re-running with
  the real coordinates and geographic filtering on cleared out the two
  implausible candidates, confirming the filter works — but didn't
  change the House Finch's score, so location wasn't the cause of the
  missed detection, just an accuracy issue worth fixing anyway.
- `birdnet.database_minimum_confidence` lowered 0.60 → 0.45 to capture
  real-but-faint calls like this one, while `slideshow_minimum_confidence`
  stays at 0.75 so weak detections are stored but never reach the
  slideshow.

With both changes, `bird-display analyze file` on that same recording
now correctly reports:

```
0.50  House Finch (Haemorhous mexicanus)  [0.0s-3.0s]
```

This is the first real bird identified end-to-end: physical
mic → capture → BirdNET → correct species. 0.45 is a first-pass value
from one 30s sample, not a rigorously tuned threshold — expect to
revisit it after more field data (§10.4 explicitly anticipates this).

### Linux/Raspberry Pi support (2026-09-13)

Added at the user's request, to move the deployment onto a Raspberry
Pi rather than continue on the Mac mini alone. The requirements doc
originally named the Mac mini/macOS as the sole target (singular
language) — revised to name two supported host profiles rather than
silently reinterpreting that (see the note at the top of
`Backyard_Bird_Discovery_System_Requirements.md` and §7.1/§5.1/§31.1).
The target Pi's actual specs, read directly off the device over SSH:
Raspberry Pi 4 Model B, 4 GB RAM, Ubuntu 24.04.4 LTS (Noble), aarch64,
96 GB free disk.

Turned out to need very little core-logic change — `capture_service.py`,
`segmenter.py`, `retention.py`, the database layer, image providers,
and the web dashboard were already OS-agnostic (plain PortAudio/SQLite/
HTTP, no macOS APIs). The actual macOS-only surface was narrow: the
`doctor.py` platform gate (hard-failed on anything but Darwin),
`install.sh` (same hard fail, plus brew-only Python provisioning), and
BirdNET's runtime backend pin.

The backend pin is the interesting part: `birdnetlib` tries
`import tflite_runtime.interpreter` first and only falls back to
`tensorflow.lite` if that's unavailable (checked directly in the
installed package, not assumed). The Mac mini profile pins `tensorflow`
because `tflite-runtime` has no official macOS arm64 wheel; Linux
aarch64 does have one, and it's far lighter — the right choice for a
4 GB Pi. `pyproject.toml` now picks the backend per `sys_platform`
marker, so `pip install -e .` just does the right thing on each host
with no code change needed in the adapter itself.

`doctor.py`'s `check_platform` now accepts Linux (aarch64/x86_64) as
well as Darwin, and the audio-device/microphone-permission checks give
OS-appropriate guidance (`audio` group and ALSA on Linux, instead of
TCC System Settings). `install.sh` gained a full Linux branch: apt
system packages (`libportaudio2`, `libsndfile1`,
`python3.<minor>-venv`), and — since Ubuntu 24.04's default `python3`
is 3.12, outside the 3.9–3.11 range this project needs — an offer to
add the `deadsnakes` PPA and install Python 3.11, mirroring the
existing "offer to `brew install`" pattern on macOS rather than
inventing a new one.

One genuine positive divergence, not just a workaround: §31.1
documents that macOS blocks microphone capture for any process without
an attached GUI session, which is why the Mac mini profile needs a
LaunchAgent (not a LaunchDaemon) and why this codebase's own live mic
testing had to happen at the physical console, never over this SSH
session (see the Microphone section below). Linux's ALSA/PortAudio
stack has no such restriction — a user in the `audio` group can open
the microphone over plain SSH, console or not. On the Pi, that
restriction simply doesn't exist.

**Deliberately out of scope here:** `systemd` unit files / boot-time
autostart. That's the Linux half of §29 Phase 8, which hasn't been
built for macOS's `launchd` either yet — both platforms still rely on
`scripts/start_all.sh`/`stop_all.sh` run by hand. Worth doing once
you're ready to stop starting services manually on either host, but
kept separate from this change so Linux and macOS stay at equal
footing rather than Linux jumping ahead.

**Validated on the physical Pi (2026-09-13):** `./scripts/install.sh`
run for real on `TheSource` (the Pi itself) surfaced one genuine bug
this Mac's own test suite couldn't have caught — see "Real bug found"
below — and, once fixed, completed cleanly: apt packages installed,
`deadsnakes` provided Python 3.11.15, `pip install -e ".[dev]"`
resolved `tflite-runtime` (not `tensorflow`) as intended, and
`bird-display doctor` reported:

```
[OK  ] platform: Linux, aarch64
[OK  ] python_version: Python 3.11.15
[OK  ] birdnet: BirdNET v2.4 analyzed soundscape.wav in 66.7s (4 detection(s)).
[OK  ] required_directories: All 11 data subdirectories exist under data
[OK  ] disk_space: 94.8 GB free
[FAIL] audio_devices: No input (microphone) devices found.
```

The `audio_devices` failure is expected, not a bug — no USB microphone
is plugged into the Pi yet. Everything software-side that can be
verified without a mic attached now passes.

BirdNET correctly found the same 4 detections on this Pi as it did on
the Mac mini for the identical test clip — same species, same model
version, different (much lighter) TFLite backend. Timing is the one
number worth tracking: **66.7s to analyze the 120s test clip**, versus
**5.4s on the M1 Mac mini** (§29 Phase 1 validation, above) — roughly
12x slower, but still well inside real-time for this project's default
30-second segments (§8.2): at this ratio a 30s segment takes ~17s to
analyze, leaving real margin before the queue could back up (§19.2's
warning threshold is 10 minutes of backlog). Default concurrency
(§19.1, one BirdNET worker) should still hold on the Pi 4 — but with
far less headroom than the M1 has, so this is worth re-checking once
continuous capture is actually running and competing with the other
services (analyzer, dashboard, images-watch) for the same 4 GB RAM.
CPU/RSS memory weren't captured in this run (`doctor` doesn't report
them) the way the original Mac spike did — worth doing if tuning
concurrency later.

**Real bug found by this run, not caught by anything runnable on the
Mac:** `pyproject.toml` declared `numpy>=1.24` with no upper bound.
`tensorflow` (macOS) happens to transitively constrain `numpy<2`
itself, so the Mac never surfaced this. `tflite-runtime==2.14.0` (last
released in 2023, before NumPy 2.0's ABI break) has no such guard —
pip resolved `numpy 2.4.6` on the Pi, and BirdNET's model load crashed
with `AttributeError: _ARRAY_API not found`. Fixed by making
`numpy<2` an explicit top-level constraint rather than an accident of
tensorflow's metadata, so both platforms share one intentional floor
instead of the Linux path being quietly unprotected.

**Still open before this branch merges to `main`:** a real microphone
plugged into the Pi and `bird-display doctor`'s `audio_devices`/
`microphone_permission` checks passing against it (which, per the
divergence documented above, should work directly over this same SSH
session — no physical console needed, unlike the Mac mini).

### Data note

The Pi starts with an empty database/cache, by design — no data
migration from the Mac mini's `data/` was requested or performed. The
Mac mini's existing detection history stays on the Mac mini.

## Setup

Two host profiles are supported (requirements §7.1): a macOS host
(Apple Silicon recommended) and an Ubuntu 24.04 LTS (aarch64) Linux
host such as a Raspberry Pi 4B. Both need Python 3.9–3.11
(tensorflow's/tflite-runtime's supported range — Ubuntu 24.04's
default `python3` is 3.12, too new; see below) and
[git](https://git-scm.com) to clone this repo, and a working
microphone.

```bash
git clone <this repo>
cd birdbrain
./scripts/install.sh
source .venv/bin/activate
cp config/config.example.yaml config/config.yaml   # done automatically by install.sh if missing
```

`install.sh` detects the OS and branches accordingly:

- **macOS**: uses full `tensorflow` as the BirdNET backend (no
  official macOS arm64 `tflite-runtime` wheels exist); offers to
  `brew install python@3.11` if no compatible interpreter is found.
- **Linux (Ubuntu)**: uses the much lighter `tflite-runtime` instead
  (official aarch64 wheels exist, and it's a better fit for a
  resource-constrained host like a 4 GB Pi — `birdnetlib` prefers it
  automatically whenever it's importable); installs `libportaudio2`,
  `libsndfile1`, and the matching `python3.<minor>-venv` package via
  `apt-get`; offers to add the `deadsnakes` PPA and install Python 3.11
  if the system's default `python3` is out of range (as it is on
  Ubuntu 24.04).

`install.sh` does more than create a venv:

0. Fails fast on things no amount of retrying fixes: wrong OS, or too
   little free disk space to survive the dependency download without
   a confusing mid-install "no space left on device" error.
1. Looks for a compatible Python (3.9–3.11) before creating the venv,
   since a newer default `python3` would fail obscurely at the
   tensorflow install step. If none is found and Homebrew is
   available, it offers to `brew install python@3.11` for you.
2. Installs the project (`pip install -e ".[dev]"`).
3. Verifies BirdNET actually works — not just that pip succeeded.
   BirdNET-Analyzer's model ships inside the `birdnetlib` wheel
   itself, so there's no separate "install BirdNET" step; what
   matters is confirming the model loads and can analyze audio. If no
   sample WAV is present under `tests/sample_audio/`, it offers to
   fetch BirdNET-Analyzer's own example clip
   (kahst/BirdNET-Analyzer, Apache-2.0) to test against.
4. Runs `bird-display doctor` as a final go/no-go gate and stops with
   guidance if anything fails.

Re-run `bird-display doctor` any time to re-check the environment:
platform, Python version, BirdNET, required directories, disk space,
audio-device availability, and — separately, since listing devices
needs no permission but recording does — whether this process can
actually open the microphone.

### First-run gotchas not fully automatable

- **The macOS microphone permission prompt.** The very first time
  anything in this project tries to record (`bird-display doctor`,
  `audio test`, or `capture run`), macOS should prompt you to allow
  microphone access for your terminal app. If it doesn't prompt (some
  macOS versions silently deny instead), `doctor`'s
  `microphone_permission` check will tell you — go to **System
  Settings → Privacy & Security → Microphone** and enable it for
  Terminal/iTerm yourself.
- **The Xcode Command Line Tools popup.** If this is a fresh Mac and
  you've never run `python3`/`git` from Terminal before, the *first*
  invocation can pop up a system dialog offering to install
  "Command Line Developer Tools." That's expected — let it finish,
  then re-run `./scripts/install.sh`.
- **Intel Macs**: `doctor`'s `platform` check will warn rather than
  fail. Every pinned dependency (tensorflow included) publishes an
  x86_64 wheel, so `pip install` is expected to succeed — but this
  project is only developed and tested on Apple Silicon, so runtime
  behavior there (BirdNET performance, audio device handling) isn't
  verified. Please open an issue if you hit something on Intel.
- **The `apt-get`/`add-apt-repository` sudo prompts on Linux.** Adding
  the `deadsnakes` PPA and installing system packages needs root —
  `install.sh` shells out to `sudo` for these steps, so expect a
  password prompt (or run the script as a user with passwordless
  sudo).
- **No microphone permission prompt on Linux** — there's no TCC-style
  dialog to answer. If `doctor`'s `microphone_permission` check fails,
  it's almost always that the user isn't in the `audio` group yet
  (`sudo usermod -aG audio $USER`, then log out and back in).

Edit `config/config.yaml`: at minimum set `location.latitude` /
`location.longitude` and `audio.device_name` for your installation.

### Uninstalling

```bash
./scripts/uninstall.sh                    # stops services, removes .venv/
./scripts/uninstall.sh --purge-data       # also deletes data/ and config/config.yaml
                                           # (detection database, captured audio, images —
                                           #  confirmed interactively; add --yes to skip that)
```

## Running everything (desktop launcher)

**Start Backyard Birds.command** and **Stop Backyard Birds.command** on
the Desktop start/stop capture, the analyzer, and the dashboard
together — double-click after a cold boot to get going, no Terminal
typing required. Each is a thin wrapper around the real logic in
`scripts/start_all.sh`/`stop_all.sh` (tracked in the repo, so updating
the project updates what the launcher does); the Desktop files
themselves never need to change.

- Safe to double-click more than once — already-running services are
  detected (by PID file, cross-checked against the actual process
  command so a reused PID after reboot can't false-positive) and
  skipped rather than duplicated.
- Services keep running after you close the Terminal window that
  opens — that window is just for startup visibility (migrations
  applied, each service's PID, any immediate errors), not something
  you need to keep open.
- Logs land in `data/logs/<service>.out.log` (stdout/stderr) alongside
  the structured JSON logs each service already writes via
  `logging_config.py`.
- `bird-display images watch` starts too (§15.1) — it polls every 10
  minutes for species with no photo yet and fetches one automatically,
  so a newly detected species' image appears on the dashboard without
  you running anything manually. This isn't the same load as
  `images fetch-missing` run repeatedly by hand (see the Phase 4 notes
  above) — it only ever acts on species that actually need it.
- This is a manual launcher, not `launchd` auto-start-at-login —
  unattended startup is explicitly a later, separate piece (§29 Phase
  8), not built yet.

This needs to be run at the actual console (double-clicked locally),
same as any command touching the microphone — see the Microphone
section above for why.

## Usage

```bash
bird-display doctor
bird-display config validate
bird-display audio list-devices
bird-display audio test --seconds 5
bird-display capture run
bird-display analyze run
bird-display dashboard run
bird-display dashboard run --reload  # dev only: auto-restarts on routes.py/repositories.py edits
bird-display images fetch-missing
```

See `bird-display --help` (and `--help` on any subcommand/group) for
the full, current list — this section is deliberately short rather
than duplicating it and drifting out of date.

## Tests

```bash
pytest                              # unit tests
pytest -m integration               # also runs the real BirdNET model
                                     # (needs a sample WAV — see
                                     # tests/sample_audio/README.md)
```
