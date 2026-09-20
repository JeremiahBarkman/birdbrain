# Development History

This is the full, chronological, dated build log for the Backyard Bird
Discovery System: every phase's implementation notes, every real bug
found and fixed (many only discoverable by actually running the code
against real hardware — a real microphone, a real Raspberry Pi, a real
reboot), and how each feature was actually validated rather than just
assumed to work.

It's split out of [README.md](README.md) to keep that file a clean,
short "what is this and how do I run it" for a new visitor — this file
is for anyone who wants the real story behind a design decision, or is
picking this project back up (human or Claude Code) and wants the full
context of what's already been tried, what broke, and why something is
built the way it is.

Read top to bottom for the project's actual history, oldest first.

---

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
- `location` set to the real installation coordinates (western Oregon — redacted here for the public repo). Re-running with
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
version, different (much lighter) TFLite backend. The one-off `doctor`
check took **66.7s to analyze the 120s test clip**, versus **5.4s on
the M1 Mac mini** (§29 Phase 1 validation, above) — roughly 12x slower
— but that figure includes a cold model load, and overstates the real
per-segment cost: once `analyze run` is actually running continuously
(model loaded once, reused for every segment), real live 30-second
segments analyzed in **~3.5s each**, not the ~17s a naive linear
scaling from the one-off figure would suggest. Comfortably inside
real-time with real margin before the queue could back up (§19.2's
warning threshold is 10 minutes of backlog) — confirmed live: `queue
status` showed `incoming 0 / processing 0 / processed 9 / failed 0`
after several minutes of continuous capture, nothing backing up.
Default concurrency (§19.1, one BirdNET worker) holds fine on the Pi
4. CPU/RSS memory weren't captured the way the original Mac spike did
— worth doing if tuning concurrency further, but not blocking given
how much margin the 3.5s number already leaves.

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

**Second real bug, found the same day the microphone was actually
connected:** a Pi power-cycle (moving the device) renumbered the
mic's ALSA hardware index from `hw:1,0` to `hw:3,0` — USB card indices
are assigned by enumeration order at boot on Linux, unlike macOS's
stable CoreAudio names, and PortAudio bakes that index straight into
the device name it reports. The exact-string match in
`find_input_device` (working as designed — §26.1's retry-with-backoff,
never crashing) could never have recovered from this on its own: the
configured name was permanently "not found" until someone noticed and
re-edited `config.yaml`, which defeats §20's "recover automatically
after reboots" specifically on this host profile. Fixed by falling
back to a match with the trailing `(hw:N,M)` suffix stripped from both
sides when the exact match fails — a config written against one boot's
index now resolves fine after a later reboot renumbers it. 6 new unit
tests; macOS names never have this suffix, so the fallback is a
no-op there.

**Fully validated live, end to end, 2026-09-14:** with the microphone
reconnected and both bugs above fixed, `capture run` → `analyze run`
produced genuine detections from real outdoor bird calls at the
installation site — American Robin (0.67 confidence) and Cedar Waxwing (0.57) — via
`bird-display detections today`. Every §19.2 queue-health number was
clean throughout (`incoming 0 / processing 0 / processed 9 / failed
0`). This branch is ready to merge to `main`.

### Data note

The Pi starts with an empty database/cache, by design — no data
migration from the Mac mini's `data/` was requested or performed. The
Mac mini's existing detection history stays on the Mac mini.

### Interactive setup wizard (2026-09-14)

Prompted directly by the hour spent debugging exactly this class of
problem on the Pi above: `location.latitude/longitude` and
`audio.device_name` both fail *silently* when wrong, not loudly. A
placeholder or wrong location doesn't error — §10.3's geographic
filter just quietly excludes real local species (or admits implausible
ones). A placeholder or stale device name doesn't error either —
`capture_service.py` retries forever with backoff exactly as §26.1
specifies, which is correct behavior, but from the outside it just
looks like "nothing is happening," and a reboot can silently invalidate
a previously-correct name on Linux (the ALSA `hw:N,M` renumbering bug
above). A new install shouldn't have to rediscover either the hard way,
especially someone less inclined to go digging through JSON log lines
than this session did.

`bird-display setup` (`src/backyard_bird/setup_wizard.py` for the
testable logic, wired into `cli.py`) walks through both:

- **Location**: prompts for a city/state or ZIP, geocodes it via
  [Nominatim](https://nominatim.openstreetmap.org) (OpenStreetMap's
  free geocoder — no API key, so a new user isn't blocked on getting
  one just to finish setup), shows the resolved coordinates and place
  name for confirmation before saving. Sends a descriptive User-Agent
  per Nominatim's usage policy — the exact same lesson this project
  already paid for once with Wikimedia's image API returning a silent
  403 (Phase 4 notes, above); no reason to relearn it here.
- **Microphone**: lists real detected input devices (reusing
  `audio/devices.py`) and lets you pick one by number, rather than
  typing a name blind. Auto-suggests when there's exactly one.

Both write into the existing `config.yaml` via `set_config_value` — a
targeted regex substitution of just the one line, not a full
parse-and-rewrite. Deliberate: a full YAML round-trip would need a
comment-preserving library (e.g. `ruamel.yaml`) as a new dependency to
avoid silently stripping `config.example.yaml`'s field-documentation
comments, which is exactly the kind of dependency CLAUDE.md's rule 5
("don't introduce dependencies without a clear reason") asks to avoid
when a much simpler approach covers the actual need. Whatever value is
written is passed through PyYAML's own dumper to decide whether it
needs quoting — chosen specifically because hand-rolled quoting rules
are exactly how this project shipped the ALSA-device-name bug above in
the first place (`TONOR G11 USB microphone: Audio (hw:1,0)`, written
unquoted, broke YAML parsing outright). Verified live against the real
Nominatim API (not just mocked in tests) and the real CLI command,
end to end, before being wired into `install.sh`.

`install.sh` now offers to run it automatically right after creating
`config.yaml` from the example — only for a freshly created config, so
re-running the installer never clobbers a setup someone already tuned
by hand. 15 new unit tests (`test_setup_wizard.py` for geocoding/config
editing, `test_cli_setup.py` for the interactive prompt flow).

**Real gotcha found running this live on the Pi, the first time anyone
actually typed a ZIP code into it:** the installation's real 5-digit
US ZIP code geocoded to **Kretinga, Lithuania** — a bare postal code
with no country/state context is globally ambiguous, and Nominatim's
top match wasn't even close. The confirm-before-saving step (always
shown, including the full `display_name` — "Lietuva" was right there
in the output) caught it correctly and cost nothing but a retry with a
specific city/state query instead of the bare ZIP. Rather than treat
"it got caught" as sufficient on its own, the prompt now says so
explicitly up front — "'City, State' is least ambiguous... a bare
ZIP/postal code can match a different country" — so a new user sees
the warning before typing, not just the confirmation after.

**Second real gotcha, found immediately after fixing the first one:**
the wizard's own microphone picker reported "No input devices found"
on the Pi — with the actual TONOR mic verifiably connected (`lsusb`
showed it, `/proc/asound/cards` showed it registered as ALSA card 3).
Root cause, confirmed by directly testing the hypothesis: `capture run`
was already running as a background service and had the device open.
ALSA's raw `hw:N,M` device nodes (unlike `plughw:`/`default`) don't
support concurrent access — not even a second process just *querying*
capabilities, let alone recording — so an already-running capture
service alone is enough to make `list_input_devices()` return empty
for that device to any other process, including `bird-display setup`,
`audio list-devices`, and `doctor`. Confirmed directly: stopping
services (`./scripts/stop_all.sh`) made the device reappear
immediately, with no other change. This is Linux/ALSA-specific — it
has no equivalent on the macOS/CoreAudio profile. Both `setup`'s
zero-devices message and `doctor`'s `audio_devices` Linux hint now say
so explicitly, rather than just suggesting "check the connection" for
a cause that wasn't the connection at all.

### Auto-start on boot (2026-09-14)

The second piece of the "make Linux install easy for public GitHub
users" effort (after the setup wizard, above): `bird-display services
install` completes requirements §29 Phase 8, which had sat undone
since Phase 1 — until now, *both* platforms only had the manual
`scripts/start_all.sh`/`stop_all.sh` launcher, with nothing surviving
a reboot unless someone ran it by hand.

- **Linux**: one systemd unit per service (`capture`, `analyzer`,
  `images-watch`, `dashboard`), written to `/etc/systemd/system/`
  (system-level, needs `sudo`) and enabled with `Restart=on-failure` —
  each independent, so one crashing repeatedly doesn't take the others
  down (§20.1). System-level rather than a `--user` unit specifically
  to avoid needing `loginctl enable-linger` for services to start
  before any login on a headless boot.
- **macOS**: one launchd **LaunchAgent** per service, under
  `~/Library/LaunchAgents/` (no `sudo` — user-writable). A LaunchAgent,
  never a LaunchDaemon, on purpose: §31.1 already established that
  macOS blocks microphone capture entirely for any process without an
  attached GUI/WindowServer session — a LaunchDaemon (system-level, no
  GUI session) would hit that exact wall permanently, the same one
  that made this project's own early mic testing have to happen at the
  physical console rather than over SSH.
- `bird-display doctor` gained a `service_autostart` check — warns
  (never fails) if auto-start isn't installed, since the manual
  launcher remains a fully supported alternative.
- `bird-display services status` (previously unimplemented, though
  listed in §25 since the original spec) now does something real:
  reports each service's installed/running state.

Rendering the unit files/plists (`src/backyard_bird/service_install.py`)
is pure and fully unit-tested (16 new tests across
`test_service_install.py`, `test_cli_services.py`, and `doctor.py`'s
new check) — no root or a real service manager needed to verify the
generated content is correct. Actually installing them
(`subprocess`-calling `sudo systemctl`/`launchctl`) is real
system-changing, boot-affecting behavior, so unlike the setup wizard
this wasn't switched on unprompted on either the Mac or the Pi — it's
built and tested, waiting on you to run `bird-display services
install` on each host when you're ready.

Also fixed along the way: `scripts/start_all.sh`'s summary always
printed `http://127.0.0.1:8765` for the dashboard regardless of actual
config — already wrong the moment `dashboard.host` is `0.0.0.0` for
LAN access (confirmed live: the dashboard's own log line correctly
showed the Pi's real LAN address while `start_all.sh`'s summary lied
about it one line below). Fixed by adding `bird-display dashboard url`
(reusing the same resolution logic `dashboard run` already had,
instead of a second copy in bash) and having the script call it. Also
fixed: the "double-click 'Stop Backyard Birds' on the Desktop" line —
Mac-only framing that made no sense on a headless Ubuntu box with no
Desktop at all — is now conditional on the actual OS.

**Real bug found on the very first live `services install` run, on the
Pi:** once `capture` auto-starts and holds the mic open permanently —
the whole point of this feature — `bird-display doctor`'s
`audio_devices` check would `FAIL` **forever after**, in the normal,
healthy, steady state, because ALSA's raw `hw:N,M` exclusivity
(documented above) makes a device already held by `capture run` look
identical to "no mic connected" to any other process, including
`doctor` itself. `doctor` exits nonzero on any `FAIL`, so this would
make `doctor` permanently report failure after every successful,
correct install — a real trust problem for exactly the audience this
whole effort is for. Fixed: `check_audio_devices` now checks whether
`capture run` is already active (via `pgrep`, bundled on both target
OSes — no new dependency) before concluding "no devices" is a real
problem, and reports `WARN` with an accurate explanation instead of
`FAIL` when that's the cause. 4 new unit tests. Re-verified live on the
Pi — confirmed correct (`audio_devices: WARN` with the expected
message) — but that same live check also surfaced a second, much more
serious bug, below.

**Second, more serious real bug — the systemd units never actually
worked, for a completely unrelated reason:** `systemctl status` showed
every service stuck in `activating (auto-restart)` with `status=203/
EXEC` — systemd's own code for "could not execute the specified
command" — and `journalctl` showed 55+ failed restart attempts, one
every `RestartSec=10`, since the moment `services install` ran.
Root cause: `_venv_bin()` computed the venv's `bin/` directory as
`Path(sys.executable).resolve().parent` — but a venv's `python`
binary is typically a *symlink* to the base interpreter that created
it (`.venv/bin/python3.11 -> /usr/bin/python3.11`), and `.resolve()`
follows symlinks to their real target. The rendered unit ended up with
`ExecStart=/usr/bin/bird-display` — a path that doesn't exist —
instead of the venv's own `bird-display` script. Fixed by dropping
`.resolve()`: `sys.executable`'s reported path already lives inside
the venv's `bin/`, resolving it was never necessary and actively wrong
here. 1 new regression test (`test_venv_bin_does_not_resolve_symlinks`,
simulating exactly this symlink shape without needing a real venv).

Worth being honest about the process failure here, not just the code
fix: this bug shipped in the same commit as the feature, wasn't caught
by 16 passing unit tests (none of which exercised the actual path
computation against a realistic symlink), and was only found because
`systemctl status`/`journalctl` were checked directly after
`bird-display services status` alone kept reporting `activating` —
a state whose ambiguity (mid-restart? genuinely stuck?) should have
been chased immediately with real systemd diagnostics rather than
assumed transient on the first look.

**Fully validated live, end to end, including a real reboot
(2026-09-14):** with the `_venv_bin()` fix applied and `services
install` re-run, all four services reached `active (running)` for
real (new PIDs, correct `ExecStart`, capture correctly logging the
resolved mic). Then the actual Phase 8 exit condition was tested for
real, not assumed: `sudo reboot`, wait, SSH back in fresh, and check —
without running `start_all.sh` or anything else by hand. Result: all
four services already `active`, real new PIDs with `?` TTY (systemd-
managed, not from any shell), and `bird-display detections today`
showing genuine, diverse species accumulated automatically since
boot — House Finch, Anna's Hummingbird, Canada Goose, Cedar Waxwing,
American Goldfinch, Pine Grosbeak, Northern Flicker, American Robin —
across roughly 70 minutes of fully unattended operation, queue clean
throughout (`0 incoming / 0 processing / 137 processed / 0 failed`).
This branch is ready to merge to `main`.

### Live mic status, level meter, and "Listen Live" (2026-09-14)

User-requested: mic detection info and a live level monitor near the
top of the dashboard, plus a button to actually hear what's happening
outside right now. Closes part of §22.1's original dashboard spec
("Microphone status," "Current capture state") that had simply never
been built, and adds two things beyond that literal list — a level
meter and true live audio — at the user's request.

**The real architectural constraint this ran into immediately:** the
dashboard is a separate process from capture, and on Linux, ALSA's raw
`hw:N,M` device nodes only allow *one* process to hold the microphone
at all — confirmed live on the Pi earlier tonight (the ALSA-
exclusivity finding above). So the dashboard flatly cannot open the
mic itself for a level reading or a live stream. Both features had to
be built as: capture_service.py (the one process that already owns the
device) produces the data, and the dashboard relays/proxies it.

**Level meter** — the simpler half, polled ~1x/second per the chosen
design (plain `fetch()`+`setInterval`, no WebSockets/SSE, matching
this project's existing polling philosophy): `audio/levels.py`
computes a peak level from each captured chunk and writes it to a
small JSON status file (`data/run/mic_status.json`), atomically
(temp+rename, §8.4's pattern), roughly once a second.
`GET /api/mic-status` reads it, treating a missing/malformed/stale
(>5s old — capture crashed or never started) file as "unknown" rather
than erroring. Deliberately a file, not the `service_health` table
§12.10 describes — that table doesn't actually exist yet (no migration
ever created it) and is designed for once-a-minute-per-service
snapshots anyway, not sub-second data from one specific service.

**Real bug caught by its own test:** `np.abs()` on a raw int16 chunk
containing `-32768` (a real, valid sample — silence-adjacent audio can
hit it) overflows, since `+32768` isn't representable in int16 and
two's-complement wraps it. Fixed by upcasting to int32 before `abs()`.
Found because the test suite deliberately included that exact edge
value, not by code review.

**Live audio** — the bigger half: `audio/live_monitor.py` gives
capture_service.py a small `127.0.0.1`-only TCP relay
(`socketserver.ThreadingTCPServer`, stdlib only, no new dependency).
Every captured chunk is broadcast to whichever clients are currently
connected; each gets its own small bounded queue so one slow listener
can only ever glitch *its own* audio, never affect capture, the
analyzer, or any other listener (same drop-oldest-on-full philosophy
as the main capture queue). If the port can't be bound, the whole
feature just silently doesn't exist for that run — live monitoring
must never be able to prevent capture from working, the same principle
CLAUDE.md already states for images/frame delivery.

`GET /api/monitor/live` (Flask) connects to that local relay on
request and streams the bytes back as a live, indefinite-length WAV:
a plain 44-byte PCM header (`audio/wav_stream.py`) with the RIFF/data
chunk sizes set to `0xFFFFFFFF` instead of a real byte count — the
standard trick for streaming audio through a container format that
normally wants to know the length upfront. Verified against Python's
own `wave` reader (a real WAV parser, not just "bytes that look
right") before trusting it. The dashboard's "🔊 Listen Live" button
toggles an `<audio>` element's `src`; stopping clears `src` and calls
`load()` rather than just `pause()`, since that's what actually aborts
the underlying connection and lets the relay notice the listener is
gone.

`audio.enable_live_monitor: false` turns the whole thing off. Worth
knowing if this is ever revisited: streaming raw outdoor audio is a
meaningfully bigger privacy/security exposure than aggregate detection
counts, if the dashboard is ever opened to a LAN with untrusted
devices on it — §23.3 still has no authentication by design/prototype-
status decision (see the LAN access notes above).

35 new tests across `test_levels.py`, `test_live_monitor.py` (real
localhost sockets, not mocked — this module's whole job is socket
plumbing), `test_wav_stream.py`, and the `capture_service.py`/
`test_web.py` additions, including one real end-to-end test that
connects an actual socket client to a real `CaptureService` instance
and confirms it receives the exact bytes being captured. Verified live
against the real running Mac dashboard (the `/api/monitor/live` 503
path, since no mic is connected to this Mac right now) — the actual
happy path (real audio, a real listener hearing real outdoor sound)
still needs verification on the Pi, where a microphone is actually
connected.

### Per-species spectrogram thumbnail (2026-09-18)

User-requested, inspired by the spectrogram view in
[birdnet-go](https://github.com/tphakala/birdnet-go): a visual PNG
spectrogram of each species' `best_recordings` clip, shown next to its
audio player in the species table, with a thin playhead line that
tracks playback.

Deliberately not a live/streaming spectrogram (§32's "Live spectrogram
dashboard" future-expansion item is a different, bigger feature — a
real-time view of the mic feed) — this is a static image of the one
clip this project already keeps per species, generated once whenever
that clip is (re)written.

**No new dependency, and no migration.** birdnet-go generates its
spectrograms by shelling out to `ffmpeg`/`sox`; this project instead
computes a short-time Fourier transform directly with `numpy` (already
a direct dependency throughout `audio/`) and rasterizes it with
`Pillow` (already direct in `images/`) — `audio/spectrogram.py`'s
`generate_spectrogram()`. No schema change either: `species_
spectrogram_path()` (`audio/clips.py`, alongside the existing
`species_clip_path()`) derives a fixed `spectrogram.png` path from the
species slug, the same convention `clip.wav` already uses, so nothing
new needs storing in `best_recordings` — the dashboard route checks
whether the file exists rather than reading a column.

Colorized with a small hand-picked 5-stop "magma"-like gradient
(`_COLORMAP_STOPS`, linearly interpolated per channel) rather than
pulling in matplotlib for one colormap. Verified by generating a
spectrogram of a synthetic 2 kHz test tone and confirming visually that
the output is a single bright band at the expected frequency on an
otherwise black image, not just "a PNG got written."

Wired into `analysis/worker.py`'s `_maybe_update_best_recording()`
*after* the clip extraction and `best_recordings` DB update, in its own
try/except — a spectrogram is a presentation nicety, not part of the
detection record, so (per CLAUDE.md's "image/frame failures must never
stop detection" principle, extended here to this too) a bug in FFT/PNG
generation must never prevent a real detection's clip from being
recorded. Covered by a dedicated test that monkeypatches
`generate_spectrogram` to raise and confirms the `best_recordings` row
and clip file still land correctly.

`web/routes.py` serves it at `GET /media/audio/<slug>/spectrogram.png`
and adds `spectrogram_url` (`None` when the file doesn't exist yet —
generation is best-effort, so a still-processing or previously-failed
species must render as "no spectrogram," not a broken `<img>`) to the
existing `recording` JSON. Species deletion (`DELETE
/api/species/<name>`) now removes the whole per-species clip directory
(`shutil.rmtree`) instead of unlinking just `clip.wav`, so a deleted
species doesn't leave an orphaned spectrogram file behind.

On the frontend, `dashboard.js`'s `recordingCellHtml()` renders the
image above the existing `<audio>` control when a `spectrogram_url` is
present, and a new delegated `timeupdate` listener (same capture-phase
delegation pattern already used for `play`/`pause`, since none of
these three events bubble) moves a `.spectrogram-playhead` div across
it as the clip plays.

New tests in `test_spectrogram.py` (PNG validity, directory creation,
overwrite-not-append, silence, stereo downmix, a clip shorter than the
FFT window, and rejecting non-16-bit-PCM input) plus additions to
`test_worker.py` and `test_web.py`. Not yet verified against a real
BirdNET-recorded bird call on either host — only against synthetic WAV
fixtures (a pure tone and silence) and a hand-inspected PNG.

### Adjustable gain and a live "Listen Live" spectrogram (2026-09-18)

Two user requests, both about the same underlying complaint: the mic's
input level runs quite low by default and there was no way to see or
fix that beyond squinting at the level meter.

**Gain** (`audio/gain.py`): a plain linear multiplier applied to every
captured chunk in `capture_service.py`'s main loop, upstream of the
level meter, the live-monitor relay, and the actual WAV segments
written to `data/audio/incoming/` — so raising it fixes the level
BirdNET itself analyzes, not just what the dashboard displays.
Adjustable live from a slider next to the level meter without
restarting capture: `GET`/`POST /api/mic-gain` read/write a small JSON
control file (`data/run/mic_gain.json`) that capture polls about once
a second, the same atomic-write pattern `audio/levels.py` already used
in the other direction (capture -> dashboard) for the status file.
`audio.gain` in config.yaml is only the startup value. Deliberately
*not* persisted back into config.yaml on every slider move — unlike
the device switch below, this is expected to be adjusted often while
watching the meter, and the control file already survives ordinary
restarts on its own.

Samples are clipped (`np.clip`) rather than left to overflow: an
early version multiplied and cast straight to int16, and boosting a
near-full-scale sample wrapped around via two's-complement instead of
clipping — sounds far worse. Covered in `test_gain.py`.

**Live spectrogram**: a real-time waterfall view shown while "Listen
Live" plays, drawn entirely client-side with the Web Audio API's
`AnalyserNode` onto a `<canvas>` — no server-side encoding, no new
load on the Pi. Reuses the same 5-stop magma-like color gradient
`audio/spectrogram.py` uses for the static per-species PNGs
(reimplemented in JS; there's no shared code between a Python PNG
renderer and a browser canvas). One real gotcha: the `AudioContext`
must be created synchronously inside the button's own click handler
(`ensureLiveAudioGraph()`), not inside `play()`'s `.then()` — browsers
refuse to let an `AudioContext` start outside an actual user-gesture
callback, and a promise continuation no longer counts as one even
though it originated from a click. Once `createMediaElementSource()`
is called on the `<audio>` element, its default output is silently
cut off unless the analyser is explicitly reconnected to
`audioCtx.destination` too — easy to miss and the symptom (dead
silence, no errors) doesn't point at the cause.

Both verified live against the real running dashboard on the Pi: the
gain slider visibly moved the level meter and the actual captured WAV
amplitude (checked by hand against a freshly-written `processed/`
segment), and the live spectrogram rendered and scrolled correctly in
a real browser.

### Mic device dropdown (2026-09-19)

User request: "in case there is more than one mic" — the mic name next
to the capturing indicator was plain text, requiring a hand-edit of
`config.yaml`'s `audio.device_name` (plus a full capture restart) to
switch. Now a `<select>` (`#mic-device-select`), populated once at
load from `GET /api/mic-devices` (`audio/devices.py`'s existing
`list_input_devices()` — querying device metadata doesn't require
holding the mic open, so this is safe to call from the dashboard
process even while capture already has a device open).

Switching devices does **not** restart the capture process. It reuses
`capture_service.py`'s existing periodic device-presence check
(`_DEVICE_PRESENCE_CHECK_SECONDS`, originally built to notice an
unplugged mic and trigger a reconnect): that check now also polls a
small control file (`audio/device_control.py`, same pattern as gain's)
and, if the dashboard has requested a different device, treats it
exactly like the existing reconnect path — *except* as a clean
`return` rather than a `raise`, so it skips the backoff wait and the
"error" status a real disconnect gets, since a deliberate switch isn't
a failure. `run()`'s outer loop, already written to reopen the stream
after any `_capture_until_error` call ends, picks the new device name
straight back up with no other changes needed — the whole feature
turned out to be a few lines in an existing state machine, not a new
one.

Unlike gain, a device switch *is* persisted into config.yaml
(`audio.device_name`, via `setup_wizard.set_config_value` — already
used for exactly this field, from the `setup` wizard and CLI), because
picking a mic is a discrete, deliberate, rarely-changed choice where
reverting to a stale config value on the next reboot would be a real
annoyance, unlike a gain value that's expected to be nudged often.
`create_app()` gained an optional `config_path` parameter for this —
optional so every existing test/caller that doesn't pass one keeps
working exactly as before, just without config persistence (the live
control-file switch still works either way).

If the configured/selected device isn't currently one PortAudio can
see (unplugged, or ALSA renumbered it after a reboot — see the Linux
support notes above), the dropdown adds it as an extra "(not
detected)" option rather than silently jumping the selection to
whatever device happens to be first.

Covered by `test_device_control.py`, capture-service tests proving a
switch actually reconnects onto the new device with no error status
and no backoff delay, and web-route tests for both endpoints including
the config.yaml persistence path.

### Spectrogram axes, dB key, and high-pass filtering (2026-09-19)

User request, modeled on birdnet-go's spectrogram viewer: the recording
modal gained frequency (kHz) and time (seconds) axes plus a dB color
legend around the spectrogram, and a real high-pass filter — a Web
Audio `BiquadFilterNode` actually filters what you hear, and the
visible spectrogram is re-rendered server-side on demand
(`?highpass=<hz>` on the existing `/media/audio/<slug>/spectrogram.png`
route, `audio/spectrogram.py`'s new `render_spectrogram_bytes()`) so
what you see matches what you hear. The filter selection is remembered
per species (`best_recordings.highpass_hz`, migration 004), resetting
only when a higher-confidence detection replaces that species' clip —
same reset trigger `is_approved` already used.

**Real bug caught by its own test:** the highpass implementation
originally computed the brightness `reference` (the max magnitude used
to normalize dB) *after* zeroing the filtered-out bins. Filtering out a
clip's one loud frequency band left only quiet noise-floor residue
behind, and normalizing against *that* residue's own max made it
rescale to look just as bright as the original signal — a highpass
cutoff above a test tone's frequency should darken the image, and
instead brightened it. Fixed by computing `reference` from the
unfiltered spectrum before any zeroing.

Live "Listen Live" got the same axes/key, plus a high-pass filter
inserted *upstream* of the analyser node in the Web Audio graph —
unlike the static per-clip PNG, the live waterfall needs no server
round trip to reflect a filter change, since it just reads from
whatever the graph already carries. Added a 1x/2x/3x size control
(scales the canvas's CSS height and rebuilds its pixel buffer at the
new size). The whole live-spectrogram panel — chart, axes, key, and
both new controls — now folds away entirely while "Listen Live" is
inactive (previously only the canvas itself was hidden).

### Safari couldn't play the live-monitor stream (2026-09-19)

A real tester hit `NotSupportedError` in Safari the instant "Listen
Live" called `.play()` — Chrome and Firefox were unaffected.
`audio/wav_stream.py`'s streaming WAV header declares its RIFF/data
chunk sizes as a placeholder (the real length isn't known upfront for
an open-ended stream) — 0xFFFFFFFF, the max *unsigned* 32-bit value.
Read as a *signed* 32-bit size, which is what Safari's media pipeline
apparently does internally, that value is -1 — already invalid, and
apparently enough for Safari to refuse the resource outright rather
than tolerate it the way Chrome/Firefox do. Switched the placeholder
to 0x7FFFFFFF (max signed 32-bit, unambiguous either way signedness is
read) — the same value other streaming-WAV servers (Icecast/Shoutcast
WAV relays) use for this exact cross-player reason. Added a test
pinning the literal header bytes so this can't silently regress back
to the unsigned-max value.

### Species table showed average confidence, not highest (2026-09-19)

Real bug, reported live by the user from a screenshot: "Most Recent
Detection" showed a Cedar Waxwing at 81%, but that same species' row in
the table below showed 76%. `list_species_summary`'s SQL was
`AVG(d.confidence)` across every one of that species' surviving
detections — for a species with thousands of detections, a single
fresh high-confidence one barely moves the average, so the table
number looked stale/wrong compared to what just happened. Changed to
`MAX(d.confidence)`, renamed `SpeciesSummaryRow.avg_confidence` ->
`highest_confidence` (matching the naming `daily_species_summary` and
`slideshow.order: highest_confidence` already use elsewhere in this
project), so the table now agrees with "most recent" and with
`best_recordings.confidence` (which was already tracking the max, just
for the audio clip rather than the displayed number).

No test had ever caught this: every existing `list_species_summary`
test happened to filter down to exactly one surviving detection per
species, where `AVG` and `MAX` are indistinguishable by construction.
Added a case with two surviving detections in one group specifically
to make that distinction visible going forward.

### Frame adapter package (§29 Phase 6, 2026-09-20)

The physical WF1561's firmware inventory (§31.3) was collected from
its Settings screen: Android 8.1 on a Rockchip RK3326 SoC, "Uhale"
firmware. Confirms this is one of many white-label frames running the
same Uhale platform under different branding — a real, non-obscure
ecosystem, though no public/documented Uhale API has been confirmed
for this project (§30 rule 24 still applies: don't assume one exists).
Serial number, MAC addresses, and Terminal ID were also on that
screen; none of that is recorded anywhere in the repo (§23.4).

Before this, `frame/` didn't exist as a package at all — not even the
`PhotoFrameAdapter` interface §17.4 already specifies. Built the whole
thing standalone from the slideshow builder (§29 Phase 5, still not
built): `frame/manifest.py` defines `SlideshowManifest`/
`SlideshowManifestItem` — the shape `publish_slideshow()` already
commits to taking per §17.4, invented now so the adapter package isn't
blocked on Phase 5 landing first, with a docstring flagging that Phase
5 should either produce exactly this shape or this module gets
revisited alongside it.

`frame/base.py`: the `PhotoFrameAdapter` ABC plus its four result
dataclasses, straight out of §17.4. `frame/adapters/unconfigured.py`:
the safe default when `photo_frame.adapter` is unset or unrecognized —
every method returns a failure result rather than raising, so a typo
in config.yaml shows up in `frame test`/`frame inspect` output instead
of crashing whatever calls it later (a delivery scheduler doesn't
exist yet either, but §20.1's "frame failure must never interrupt
detection" is the same principle). `frame/adapters/local_export.py`:
the one adapter that must always work (§30 rule 29) — copies a
rendered slideshow directory into `data/frame-export/current/` plus
`manifest.json`/`README.txt`, using the same "build alongside in a
`.building` sibling, then swap into place" pattern as §8.4's segment
files and the spectrogram PNGs, except a directory can't be replaced
in one atomic syscall the way a file can, so it's two renames (old ->
`.previous`, new -> current) instead of one. `frame/service.py`:
picks the adapter from `photo_frame.adapter`, same registry-lookup
shape as `cli.py`'s `_build_image_providers()`.

Wired up `bird-display frame test`/`frame inspect` (§25 — both were
listed as required CLI commands from the start, just never
implemented). Verified live against the real repo's config: `frame
test` reports the export directory writable, `frame inspect` reports
"No export yet" (correct — nothing has ever called
`publish_slideshow()` outside tests), and no stray files were left
behind by the write-probe.

20 new tests (manifest shape, both adapters including the two-rename
swap actually replacing old content, the registry, and the CLI
commands through a real invocation) — all against synthetic slideshow
directories, since no real slideshow builder exists yet to generate
one from actual detections.

**What's still not done:** Uhale Web workflow verification (no
browser-pairing option confirmed present in this unit's Settings yet),
external-media import verification on the physical unit, and anything
that actually calls `publish_slideshow()` outside a test — that needs
the Phase 5 slideshow builder first.

