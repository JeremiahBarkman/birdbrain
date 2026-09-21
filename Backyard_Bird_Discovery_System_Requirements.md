# Backyard Bird Discovery System

## Software Requirements and Architecture Specification

**Document status:** Initial implementation requirements  
**Target development environment:** Claude Code  
**Target hosts:** Apple Mac mini with Apple M1 and 16 GB unified memory (primary development host); Raspberry Pi 4B with 4 GB RAM (added host, resource-constrained profile)  
**Primary operating systems:** macOS; Ubuntu 24.04 LTS (aarch64) on Linux hosts  
**Primary language:** Python 3  

> **Platform addition (2026-09-13):** This document originally named the
> Mac mini/macOS as the sole target (singular "Target host"/"Primary
> operating system"). Per the user's request to move the deployment to a
> Raspberry Pi, this is revised to name two supported hosts rather than
> silently reinterpreting the original singular language. Both hosts are
> supported going forward; nothing here retires the Mac mini path. See
> §7.1, §5.1, §29 Phase 8, and §31.1 for the specifics this affects.
**Bird identification engine:** BirdNET-Analyzer  
**Primary database:** SQLite  
**Photo frame:** Euphro WF1561 family  
**Deployment model:** Local-first, continuously operating edge application

---

## 1. Project Summary

The Backyard Bird Discovery System is a local automation platform that continuously listens to an outdoor microphone, identifies bird species from their calls using BirdNET-Analyzer, records detections in a chronological SQLite database, retrieves representative photographs of detected species, creates a daily bird slideshow, and delivers that slideshow to an Euphro WF1561 Wi-Fi photo frame.

The system is intended to run continuously on an Apple M1 Mac mini with 16 GB of unified memory.

The project has four primary functions:

1. Capture outdoor audio continuously.
2. Analyze the audio locally with BirdNET-Analyzer.
3. Maintain a searchable timeline of bird detections in SQLite.
4. Generate and deliver a daily slideshow showing the bird species detected.

The initial detection goal is to identify **all bird species BirdNET can reasonably recognize at the installation location**, rather than monitoring only a predefined watch list.

---

## 2. Product Vision

The system should transform ambient backyard bird activity into a passive visual experience.

A person looking at the connected photo frame should see a rotating collection of the bird species recently detected outside the home. The frame should function as a living visual record of local wildlife activity.

The normal user experience should require no interaction with BirdNET-Analyzer, terminal commands, log files, SQL tools, image-search websites, or photo-transfer utilities.

> Birds call outside, the system identifies them, and photographs of those birds appear automatically on the digital frame.

---

## 3. Primary Goals

### 3.1 Functional Goals

The system shall:

- Continuously receive audio from one outdoor microphone.
- Divide the incoming audio into analyzable segments.
- Process the segments locally using BirdNET-Analyzer.
- Record detections with timestamps, species names, and confidence scores.
- Maintain a chronological detection history.
- Distinguish individual detections from unique daily species.
- Search approved online sources for representative bird images.
- Download, validate, and cache selected images locally.
- Build a daily slideshow containing the species detected that day.
- Deliver or synchronize the slideshow with the Euphro WF1561 frame.
- Recover automatically after reboots and temporary failures.
- Operate without routine user intervention.

### 3.2 Quality Goals

The system should be:

- Local-first
- Reliable
- Recoverable
- Resource-conscious
- Observable
- Modular
- Testable
- Easy for Claude Code to maintain
- Respectful of image licensing and attribution
- Independent of a single image provider
- Isolated from photo-frame delivery failures

---

## 4. Scope

### 4.1 Included in Version 1

Version 1 includes:

- One M1 Mac mini host
- One outdoor microphone
- Continuous or near-continuous recording
- BirdNET-Analyzer integration
- Species-level identification
- Confidence filtering
- SQLite detection timeline
- Daily species aggregation
- Online bird-image acquisition
- Local image cache
- Daily slideshow generation
- Euphro WF1561 delivery integration
- A reliable manual-delivery fallback
- Configuration files
- Structured logging
- Automatic startup using macOS `launchd`
- Basic health monitoring
- Command-line administration tools
- Optional lightweight local status page

### 4.2 Not Required in Version 1

The following are outside the initial scope:

- Multiple simultaneous microphone locations
- Individual bird recognition
- Exact bird counting
- Bird age or sex classification
- Nest monitoring
- Video capture
- Cloud-hosted BirdNET inference
- Native iOS or Android applications
- Public social-media publishing
- Custom machine-learning model training
- Full ecological population analysis
- Guaranteed elimination of false positives
- Unofficial modification of the photo-frame firmware
- Rooting or jailbreaking the frame

---

## 5. Recommended Technical Approach

### 5.1 Automation Platform

The project shall use **custom Python services managed by the host OS's native service manager**: macOS `launchd` on the Mac mini, Linux `systemd` on Ubuntu hosts (Raspberry Pi included). Neither has been implemented yet (§29 Phase 8) — until then, both platforms use the manual `scripts/start_all.sh`/`stop_all.sh` launcher.

A workflow platform such as n8n is not recommended as the core runtime because it would add operational overhead without improving the principal audio-analysis workflow.

Python is recommended because:

- BirdNET-Analyzer is Python-oriented.
- Python has mature audio, SQLite, HTTP, scheduling, and image-processing libraries.
- Python services are lightweight enough for the M1 Mac mini and, using the lighter `tflite-runtime` BirdNET backend, for a 4 GB Raspberry Pi.
- The services can invoke BirdNET directly or through a dedicated adapter.
- Claude Code can modify and test the components independently.
- `launchd` (macOS) and `systemd` (Linux) each provide native startup and restart behavior on their respective OS.

### 5.2 Service Model

The initial implementation shall use logically independent services:

- `bird_capture`
- `bird_analyzer`
- `bird_scheduler`
- `bird_status` — optional local dashboard

A failure in image retrieval or frame delivery must never stop audio capture or BirdNET analysis.

---

## 6. High-Level Architecture

```text
Outdoor Microphone
        |
        v
Audio Capture Service
        |
        v
Durable Segmented-Audio Queue
        |
        v
BirdNET Analysis Worker
        |
        v
Detection Normalizer and Deduplicator
        |
        v
SQLite Detection Database
        |
        +-------------------------+
        |                         |
        v                         v
Daily Species Aggregator     Local Status/API
        |
        v
Image Search Service
        |
        v
Image Validation and Cache
        |
        v
Daily Slideshow Builder
        |
        v
Photo Frame Adapter
        |
        +-------------------------+
        |                         |
        v                         v
Uhale/Web Delivery          USB or SD Fallback
        |
        v
Euphro WF1561
```

---

## 7. Target Hardware

### 7.1 Host Computer

Two host profiles are supported:

**Profile A — Mac mini (primary development host)**

- Apple Mac mini
- Apple M1 processor
- 16 GB unified memory
- macOS
- Continuous power
- Persistent local storage
- Wired Ethernet preferred
- Wi-Fi acceptable where necessary

**Profile B — Raspberry Pi (added 2026-09-13)**

- Raspberry Pi 4 Model B
- 4 GB RAM
- Ubuntu 24.04 LTS (aarch64)
- Continuous power
- microSD or USB-attached persistent storage
- Wired Ethernet preferred; Wi-Fi acceptable

The Pi's far smaller memory budget (4 GB vs. 16 GB unified) means §19's
concurrency defaults, already conservative, must not be raised on this
profile without measuring headroom first (§30 rule 30). It also changes
the BirdNET runtime choice: `birdnetlib` prefers `tflite_runtime` over
`tensorflow` when both are importable, so the Pi installs
`tflite-runtime` instead of the full `tensorflow` package the Mac mini
uses — lighter, and Linux aarch64 has official wheels for it (macOS
arm64 does not, which is why the Mac mini profile uses `tensorflow`;
see §29 Phase 1 validation notes in `README.md`).

### 7.2 Outdoor Microphone

The selected microphone should provide:

- Reliable macOS compatibility
- Stable device naming
- Mono audio support
- Weather-resistant outdoor placement or protected enclosure
- Wind protection
- Suitable gain for bird calls
- A practical cable or network path to the Mac mini
- Automatic recovery after temporary disconnection where possible

A directly connected USB microphone or a supported USB audio interface is the simplest Version 1 design.

### 7.3 Photo Frame

The supplied device label identifies the frame as:

```text
Brand: Euphro
Model: WF1561
Power input: 12 V DC, 2 A
```

The WF1561 family is documented as a 15.6-inch, 16:9, Full HD frame with a target display resolution of 1920 × 1080. Product documentation for the family describes Wi-Fi photo sharing through the Uhale platform and support for external USB or memory-card media.

Because the physical label says `WF1561`, while some documentation uses `WF1561-U`, the exact firmware and hardware variant shall be verified on the frame before the production delivery adapter is finalized.

Initial rendering target:

```yaml
photo_frame:
  manufacturer: Euphro
  model: WF1561
  target_width: 1920
  target_height: 1080
  aspect_ratio: "16:9"
  preferred_orientation: landscape
  preferred_format: JPEG
  platform_expected: Uhale
```

---

## 8. Audio Capture Requirements

### 8.1 Responsibilities

The Audio Capture Service shall:

- Identify the configured macOS input device.
- Open it using a supported sample rate and channel configuration.
- Record continuously.
- Divide audio into bounded files.
- Use overlap between adjacent files to reduce clipped bird calls.
- Write completed files atomically to a durable queue.
- Detect microphone disconnections.
- Attempt automatic reconnection.
- Record capture status through structured logs and health records.
- Prevent unlimited growth of raw audio storage.

### 8.2 Recommended Initial Format

- Container: WAV
- Encoding: PCM
- Sample rate: 48 kHz preferred
- Bit depth: 16-bit
- Channels: mono preferred
- Segment duration: 30 seconds
- Segment overlap: 3 seconds

All values shall be configurable.

### 8.3 File Naming

```text
YYYY-MM-DDTHH-MM-SS_mic-01_<sequence>.wav
```

Example:

```text
2026-08-01T06-42-30_mic-01_000184.wav
```

### 8.4 Atomic File Handling

The capture service shall write an incomplete file with a temporary suffix:

```text
2026-08-01T06-42-30_mic-01_000184.wav.partial
```

After the file is complete and flushed to disk, it shall be renamed to:

```text
2026-08-01T06-42-30_mic-01_000184.wav
```

The analysis worker shall ignore `.partial` files.

---

## 9. Durable Audio Queue

The filesystem shall serve as the initial durable analysis queue.

Recommended directories:

```text
data/audio/incoming/
data/audio/processing/
data/audio/processed/
data/audio/failed/
data/audio/clips/
```

Queue lifecycle:

1. Capture writes a completed file to `incoming`.
2. The analyzer atomically moves it to `processing`.
3. BirdNET analyzes the file.
4. Successful files move to `processed` or are deleted according to retention policy.
5. Failed files move to `failed`.
6. Attempt count and error details are stored in SQLite.

The queue must survive process restarts and host reboots.

---

## 10. BirdNET Analysis Worker

### 10.1 Responsibilities

The worker shall:

- Watch the incoming queue.
- Claim one unprocessed audio file at a time.
- Invoke BirdNET-Analyzer locally.
- Supply configured date and geographic data when supported.
- Capture returned detection candidates.
- Normalize species names and confidence values.
- Insert qualifying detections into SQLite.
- Optionally retain low-confidence candidates for diagnostics.
- Mark the source segment as processed.
- Handle corrupt or unsupported files without stopping the queue.
- Prevent one failed segment from blocking later segments.

### 10.2 Configuration

The following shall be configurable:

- Latitude
- Longitude
- Minimum database confidence
- Minimum slideshow confidence
- Sensitivity
- Species-list behavior
- Locale or common-name language
- Number of analysis workers
- Geographic filtering
- Raw-result retention
- Audio retention
- Diagnostic-output retention

### 10.3 All-Species Detection

The requirement to identify all species means:

> Analyze every captured audio segment for all bird species supported by the installed BirdNET model and permitted by the configured analysis mode.

The application shall not restrict detection to a hand-maintained watch list.

Geographic filtering should be enabled by default to reduce implausible detections, but it must remain configurable.

### 10.4 Recommended Initial Thresholds

```yaml
birdnet:
  database_minimum_confidence: 0.60
  slideshow_minimum_confidence: 0.75
  sensitivity: 1.0
  geographic_filter_enabled: true
  max_workers: 1
```

These values shall be tuned after field testing.

### 10.5 Duplicate Handling

BirdNET may detect the same call in overlapping analysis windows or adjacent overlapping recordings.

Duplicate suppression shall consider:

- Scientific species name
- Detection timestamp
- Detection start and end offsets
- Source microphone
- Confidence
- Configurable duplicate window

Recommended initial duplicate window:

```text
5 seconds
```

Raw detections may be retained, but duplicates shall not inflate the daily slideshow count.

---

## 11. Database Requirements

SQLite shall be the authoritative local record.

Database path:

```text
data/database/birds.sqlite3
```

SQLite shall use:

- Foreign-key enforcement
- WAL journal mode
- Parameterized queries
- Schema migrations
- Transactional inserts
- Indexed timeline fields
- UTC timestamps internally
- Configured local-time conversion for display

---

## 12. Proposed Database Schema

### 12.1 `schema_migrations`

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);
```

### 12.2 `audio_segments`

```sql
CREATE TABLE audio_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    microphone_id TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    recording_started_at_utc TEXT NOT NULL,
    recording_ended_at_utc TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    sample_rate INTEGER,
    channels INTEGER,
    file_size_bytes INTEGER,
    sha256 TEXT,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    processing_attempts INTEGER NOT NULL DEFAULT 0,
    birdnet_version TEXT,
    processing_started_at_utc TEXT,
    processing_completed_at_utc TEXT,
    error_message TEXT,
    created_at_utc TEXT NOT NULL
);
```

Allowed statuses:

```text
pending
processing
completed
failed
quarantined
```

### 12.3 `species`

```sql
CREATE TABLE species (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scientific_name TEXT NOT NULL UNIQUE,
    common_name TEXT,
    common_name_locale TEXT,
    taxonomic_order TEXT,
    taxonomic_family TEXT,
    birdnet_label TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
```

The scientific name shall be the preferred stable species identifier.

### 12.4 `detections`

```sql
CREATE TABLE detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audio_segment_id INTEGER NOT NULL,
    species_id INTEGER NOT NULL,
    detected_at_utc TEXT NOT NULL,
    segment_offset_start_seconds REAL NOT NULL,
    segment_offset_end_seconds REAL NOT NULL,
    confidence REAL NOT NULL,
    sensitivity REAL,
    latitude REAL,
    longitude REAL,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    duplicate_of_detection_id INTEGER,
    is_reviewed INTEGER NOT NULL DEFAULT 0,
    review_status TEXT,
    audio_clip_path TEXT,
    spectrogram_path TEXT,
    raw_result_json TEXT,
    created_at_utc TEXT NOT NULL,

    FOREIGN KEY(audio_segment_id) REFERENCES audio_segments(id),
    FOREIGN KEY(species_id) REFERENCES species(id),
    FOREIGN KEY(duplicate_of_detection_id) REFERENCES detections(id)
);
```

**Design pivot (2026-08-18, human-review audio):** `audio_clip_path`
above and `detections.extract_detection_clips`/`clip_padding_seconds`/
`retain_clips_days` (§18 config) originally implied one clip per
detection, swept after `retain_clips_days`. That was never built, and
was superseded before it was: instead, migration 003 adds
`best_recordings` — exactly one row per species (its highest-confidence
detection ever), replaced in place whenever a higher-confidence
detection arrives, with a human-settable approval star that resets on
replacement. This is deliberately not a per-detection archive — it's
what keeps clip storage at "one small file per species," not "one per
detection, capped by a retention sweep." `audio_clip_path` on
`detections` stays unused/dormant, as does `retain_clips_days`;
`extract_detection_clips` and `clip_padding_seconds` are reused as-is,
now governing best_recordings extraction instead. See
`src/backyard_bird/audio/clips.py` and
`src/backyard_bird/database/repositories.py`'s best_recordings section.

Suggested review statuses:

```text
unreviewed
confirmed
rejected
uncertain
```

### 12.5 `daily_species_summary`

```sql
CREATE TABLE daily_species_summary (
    local_date TEXT NOT NULL,
    species_id INTEGER NOT NULL,
    first_detected_at_utc TEXT NOT NULL,
    last_detected_at_utc TEXT NOT NULL,
    detection_count INTEGER NOT NULL,
    highest_confidence REAL NOT NULL,
    representative_detection_id INTEGER,
    updated_at_utc TEXT NOT NULL,

    PRIMARY KEY(local_date, species_id),

    FOREIGN KEY(species_id) REFERENCES species(id),
    FOREIGN KEY(representative_detection_id) REFERENCES detections(id)
);
```

### 12.6 `bird_images`

```sql
CREATE TABLE bird_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    species_id INTEGER NOT NULL,
    source_provider TEXT NOT NULL,
    source_page_url TEXT,
    original_image_url TEXT NOT NULL,
    local_file_path TEXT,
    image_width INTEGER,
    image_height INTEGER,
    mime_type TEXT,
    sha256 TEXT,
    photographer_name TEXT,
    license_name TEXT,
    license_url TEXT,
    attribution_text TEXT,
    search_query TEXT,
    suitability_score REAL,
    status TEXT NOT NULL DEFAULT 'candidate',
    downloaded_at_utc TEXT,
    last_verified_at_utc TEXT,
    created_at_utc TEXT NOT NULL,

    FOREIGN KEY(species_id) REFERENCES species(id)
);
```

Suggested image statuses:

```text
candidate
approved
rejected
unavailable
expired
```

### 12.7 `slideshows`

```sql
CREATE TABLE slideshows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_date TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    output_directory TEXT,
    manifest_path TEXT,
    species_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    generated_at_utc TEXT,
    delivered_at_utc TEXT,
    delivery_status TEXT,
    delivery_method TEXT,
    delivery_error TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
```

### 12.8 `slideshow_items`

```sql
CREATE TABLE slideshow_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slideshow_id INTEGER NOT NULL,
    species_id INTEGER NOT NULL,
    bird_image_id INTEGER NOT NULL,
    display_order INTEGER NOT NULL,
    title_text TEXT,
    subtitle_text TEXT,
    detection_summary_text TEXT,
    rendered_file_path TEXT,
    created_at_utc TEXT NOT NULL,

    FOREIGN KEY(slideshow_id) REFERENCES slideshows(id),
    FOREIGN KEY(species_id) REFERENCES species(id),
    FOREIGN KEY(bird_image_id) REFERENCES bird_images(id),

    UNIQUE(slideshow_id, display_order)
);
```

### 12.9 `frame_delivery_attempts`

```sql
CREATE TABLE frame_delivery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slideshow_id INTEGER NOT NULL,
    adapter_name TEXT NOT NULL,
    destination_identifier TEXT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    files_attempted INTEGER NOT NULL DEFAULT 0,
    files_delivered INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    metadata_json TEXT,

    FOREIGN KEY(slideshow_id) REFERENCES slideshows(id)
);
```

### 12.10 `service_health`

```sql
CREATE TABLE service_health (
    service_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_heartbeat_at_utc TEXT NOT NULL,
    last_success_at_utc TEXT,
    last_error_at_utc TEXT,
    last_error_message TEXT,
    metadata_json TEXT
);
```

---

## 13. Timeline Requirements

Each accepted detection shall include:

- Exact detection timestamp
- Local date
- Common species name
- Scientific species name
- Confidence score
- Source audio segment
- Offset within the segment
- Microphone identifier
- Geographic configuration used
- BirdNET version
- Optional extracted audio clip
- Optional review status

The system shall support queries for:

- Detections today
- Detections by date range
- Detections by species
- First detection of a species
- Most recent detection of a species
- Highest-confidence detections
- Most frequently detected species
- Unique species per day
- Unique species per month
- Hour-of-day activity
- Unreviewed detections
- Failed audio segments

---

## 14. Daily Species Aggregation

A scheduled aggregation job shall run periodically.

Recommended schedule:

```text
Every 15 minutes: update the current day's summary
At 12:15 a.m.: finalize the previous day's summary
```

For each local day, the job shall determine:

- Unique species detected
- Detection count per species
- First detection time
- Last detection time
- Highest confidence
- Representative detection
- Whether an approved image is available
- Whether the species qualifies for the slideshow

A species shall normally appear only once in a daily slideshow, regardless of its number of detections.

---

## 15. Image Search Service

### 15.1 Search Behavior

The system shall search for a representative image when:

- A newly detected species has no approved cached image.
- A cached image is missing, corrupt, or expired.
- The user requests an image refresh.
- The configured rotation period has elapsed.

The system shall not perform a new search every day for species with suitable cached images.

### 15.2 Search Inputs

Search terms should use, in order:

1. Scientific name
2. Common name
3. Scientific name plus `bird`
4. Common name plus region
5. Scientific name plus region

Examples:

```text
Turdus migratorius bird
American Robin Oregon
```

### 15.3 Preferred Sources

The implementation should prioritize sources with:

- Stable APIs
- Reliable species metadata
- Clear image-license information
- Photographer or rights-holder attribution
- Sufficient image resolution

Initial provider candidates:

- Wikimedia Commons
- iNaturalist
- Flickr API with appropriate license filtering
- Other approved providers added behind the same interface

The system shall avoid scraping general search-result pages when a supported API is available.

### 15.4 Image Validation

Before approving an image, the service shall verify:

- Successful HTTP download
- Accepted MIME type
- Non-empty content
- Minimum dimensions
- Usable aspect ratio
- Successful image decoding
- No exact duplicate in the cache
- Sufficient species association
- License metadata when required
- File size within configured limits

Recommended minimum source dimensions:

```text
1280 × 720
```

### 15.5 Image Ranking

Candidates should be ranked using:

- Exact scientific-name match
- Exact common-name match
- Reliable source metadata
- Resolution
- Orientation compatibility
- Subject visibility
- Absence of intrusive watermarks
- License suitability
- Prior successful use
- Regional relevance where available

### 15.6 Image Cache

```text
data/images/species/<scientific-name-slug>/
```

Example:

```text
data/images/species/turdus-migratorius/
```

The cache shall retain:

- Original downloaded image
- Optimized 1920 × 1080 frame image
- Source metadata
- Attribution
- File hash
- Approval status

---

## 16. Slideshow Requirements

### 16.1 Daily Slideshow

The system shall generate one logical slideshow for each local calendar day.

The current-day slideshow should update during the day as newly detected species qualify.

Recommended schedule:

- Update the current-day slideshow every 30 minutes.
- Finalize the prior-day slideshow at 12:15 a.m.
- Retain prior slideshows according to configuration.

### 16.2 Slide Content

Each slide shall contain:

- Representative bird photograph
- Common name
- Scientific name
- First detection time or daily time range
- Detection count
- Optional highest confidence
- Required attribution when applicable

Example:

```text
American Robin
Turdus migratorius

First heard: 6:42 AM
Detected 18 times today
```

### 16.3 Presentation Modes

#### Clean Mode

- Bird image
- Common name
- Optional scientific name

#### Informational Mode

- Bird image
- Common name
- Scientific name
- First detection time
- Detection count
- Attribution

Default:

```text
Informational Mode
```

### 16.4 Rendering for Euphro WF1561

The slideshow renderer shall initially target:

- 1920 × 1080 pixels
- 16:9 landscape
- JPEG output
- sRGB color profile
- Safe margins for text
- High-quality JPEG compression
- No reliance on unsupported animation formats

The renderer shall:

- Resize without distortion.
- Crop intelligently.
- Avoid upscaling very small source images.
- Preserve readable text.
- Place attribution unobtrusively.
- Use safe filenames.
- Produce a manifest describing the slideshow.

### 16.5 Ordering

Supported ordering modes:

- First detection time
- Most recent detection time
- Highest confidence
- Detection frequency
- Alphabetical species name
- Randomized

Recommended default:

```text
First detection time
```

---

## 17. Euphro WF1561 Delivery Integration

### 17.1 Known Constraints

The frame is expected to use the Uhale ecosystem. Available documentation describes sharing through the Uhale app and Uhale Web, along with external-media import. A stable public programming API has not yet been confirmed.

The system shall therefore use a replaceable adapter architecture and shall not embed Uhale-specific behavior into the slideshow builder.

### 17.2 Required Firmware Verification

Before implementation of unattended Wi-Fi publishing, the following shall be collected from the physical frame:

- Application or platform name
- Firmware version
- Android version, if shown
- Device name
- Local IP address
- MAC address
- Storage capacity and free space
- Supported import methods
- Uhale Web availability
- Supported browser-upload workflow
- USB and card-slot behavior
- Whether imported media is copied to internal storage
- Whether albums can be replaced or synchronized

### 17.3 Delivery Priority

The implementation shall investigate and use delivery methods in this order:

1. **Supported Uhale Web workflow** that can be automated reliably and lawfully.
2. **Documented local-network or cloud API**, if discovered.
3. **Supported browser automation**, only if stable, permitted, and isolated behind an adapter.
4. **USB or memory-card synchronization** as the dependable fallback.
5. **Manual upload package** generated by the system when no safe unattended route exists.

The project shall not depend on reverse engineering as a Version 1 requirement.

### 17.4 Adapter Interface

```python
class PhotoFrameAdapter:
    def test_connection(self) -> "ConnectionResult":
        ...

    def publish_slideshow(
        self,
        slideshow_directory: Path,
        manifest: "SlideshowManifest",
    ) -> "PublishResult":
        ...

    def remove_old_slideshows(
        self,
        retention_days: int,
    ) -> "CleanupResult":
        ...

    def get_status(self) -> "FrameStatus":
        ...
```

Initial adapters:

```text
UnconfiguredFrameAdapter
LocalExportAdapter
RemovableMediaAdapter
UhaleWebAdapter        # only after feasibility is verified
```

### 17.5 Manual Fallback Package

Until unattended Wi-Fi delivery is proven, the application shall generate:

```text
data/frame-export/current/
```

This directory shall contain:

- Final 1920 × 1080 JPEG slides
- A human-readable `README.txt`
- A machine-readable `manifest.json`
- Optional attribution file
- No temporary or intermediate files

The package shall be ready to copy to supported USB or memory-card media.

### 17.6 Delivery Failure Behavior

When delivery fails:

- Keep the last successful slideshow active.
- Record the failed attempt.
- Retry according to policy.
- Do not rebuild unchanged slides unnecessarily.
- Do not block audio capture or analysis.
- Preserve the export package for manual transfer.

---

## 18. Configuration

Configuration shall be stored outside source code.

```text
config/config.yaml
config/secrets.env
```

Secrets shall never be committed to Git.

### 18.1 Example Configuration

```yaml
system:
  timezone: America/Los_Angeles
  log_level: INFO
  data_directory: ./data

location:
  latitude: 45.0
  longitude: -123.0

audio:
  device_name: Outdoor USB Microphone
  microphone_id: backyard-mic-01
  sample_rate: 48000
  channels: 1
  segment_seconds: 30
  overlap_seconds: 3
  raw_audio_retention_days: 7
  failed_audio_retention_days: 30

birdnet:
  database_minimum_confidence: 0.60
  slideshow_minimum_confidence: 0.75
  sensitivity: 1.0
  geographic_filter_enabled: true
  max_workers: 1
  retain_raw_results: true
  duplicate_window_seconds: 5

detections:
  extract_detection_clips: true
  clip_padding_seconds: 1
  retain_clips_days: 30

images:
  preferred_sources:
    - wikimedia_commons
    - inaturalist
  minimum_width: 1280
  minimum_height: 720
  images_per_species: 3
  refresh_after_days: 90
  require_license_metadata: true

slideshow:
  update_interval_minutes: 30
  one_species_per_day: true
  order: first_detection
  display_mode: informational
  image_duration_seconds: 20
  retain_daily_slideshows_days: 30

photo_frame:
  manufacturer: Euphro
  model: WF1561
  expected_platform: Uhale
  adapter: local_export
  target_width: 1920
  target_height: 1080
  orientation: landscape
  preferred_format: JPEG
  export_directory: ./data/frame-export/current
```

---

## 19. Resource Management

The implementation shall remain conservative on the 16 GB M1 Mac mini.

### 19.1 Concurrency Defaults

- One BirdNET worker
- One image-download worker
- One slideshow-rendering task
- One frame-delivery task

Concurrency shall be configurable but shall not default to aggressive parallelism.

### 19.2 Backpressure

If analysis falls behind capture:

- Recording must continue.
- Pending files must remain queued.
- Queue depth must be logged.
- Warnings must be raised at configurable thresholds.
- The application must not launch unlimited workers.

Suggested thresholds:

```text
Warning: more than 10 minutes of queued audio
Critical: more than 60 minutes of queued audio
```

### 19.3 Disk Management

Retention policies shall cover:

- Raw audio
- Detection clips
- Failed audio
- Cached images
- Rendered slides
- Logs
- Temporary files

The SQLite database shall not be removed by automatic retention.

Recommended free-space thresholds:

```text
Warning below: 20 GB
Critical below: 10 GB
```

At the critical threshold, eligible old raw audio may be deleted before capture is stopped.

---

## 20. Reliability and Recovery

### 20.1 Service Independence

- Image-search failure must not stop detection.
- BirdNET failure must not stop audio capture.
- Frame failure must not cause detection loss.
- Dashboard failure must not stop any pipeline service.

### 20.2 Heartbeats

Each service shall update `service_health` at least once per minute.

Statuses:

```text
starting
healthy
degraded
failed
stopped
```

### 20.3 Restart Recovery

After a restart, the system shall:

1. Inspect `.partial` audio files.
2. Quarantine or repair incomplete files.
3. Reset records stuck in `processing`.
4. Resume pending analysis.
5. Verify database integrity.
6. Rebuild the current slideshow only if necessary.
7. Retry pending frame delivery.
8. Avoid duplicating completed work.

### 20.4 Idempotency

Running the same scheduled job twice must not:

- Duplicate detections
- Duplicate species rows
- Duplicate images
- Duplicate slideshow items
- Duplicate daily slideshow records
- Duplicate frame uploads unnecessarily

---

## 21. Logging and Observability

Recommended log directory:

```text
data/logs/
```

Recommended logs:

```text
capture.log
analyzer.log
scheduler.log
images.log
frame.log
application.log
```

Each structured log event should include:

- UTC timestamp
- Service
- Severity
- Event name
- Message
- Related audio segment ID
- Related detection ID
- Related species ID
- Related slideshow ID
- Error type
- Stack trace for unexpected errors

Logs shall rotate automatically.

---

## 22. Local Status Interface

A local browser-based interface is recommended after the core pipeline is operational.

Default binding:

```text
http://127.0.0.1:8765
```

### 22.1 Dashboard

The dashboard should display:

- Microphone status — **done, 2026-09-14**, plus two additions beyond this list: a live input-level meter and a "listen live" button streaming real outdoor audio to the browser (both user-requested; see README's dated implementation note for the live-audio relay architecture this needed — the dashboard can't open the microphone itself, since ALSA only allows the capture process to hold the device, confirmed live on the Pi, §31.1). **Extended, 2026-09-18** (both user-requested; see DEVELOPMENT.md's dated implementation note): a gain slider next to the level meter, adjustable live without restarting capture (the mic's default input level runs quite low, with no hardware control for it — `audio/gain.py`); and a real-time client-side spectrogram (Web Audio API + `<canvas>`, no server round trip) shown while "Listen Live" is playing — a partial, differently-scoped fulfillment of §32's "Live spectrogram dashboard" item from what the 2026-09-18 per-species-clip addition above already covers. **Extended again, 2026-09-19** (user-requested, "in case there is more than one mic"; see DEVELOPMENT.md's dated implementation note): the device name is now a dropdown (`GET`/`POST /api/mic-devices`/`/api/mic-device`) listing every input device PortAudio can see, switchable live without restarting capture (`audio/device_control.py`) and persisted into `config.yaml`.
- Current capture state — covered by the same status above (capturing/stopped/error)
- Last completed segment
- Analysis queue depth
- Last successful detection
- Species detected today
- Detection count today
- Current slideshow status
- **Added, 2026-09-21** (user-requested, beyond this list): a
  fullscreen slideshow preview. A "▶ Slideshow" button in the header
  fetches `GET /api/slideshow` (today's qualifying species — same
  confidence-threshold + approved-image rule §16.2 describes, applied
  live against `detections` rather than the not-yet-scheduled
  `daily_species_summary` table, same as `/api/stats`) and cycles
  fullscreen through them with a text overlay in the browser (Clean or
  Informational per `slideshow.display_mode`, §16.3), until any key
  press or mouse action ends it. This previews what the frame delivery
  pipeline (§29 Phase 5/6/7) will eventually show physically — it does
  not read from or write to `slideshows`/`slideshow_items`, since no
  builder populates those yet. See DEVELOPMENT.md's dated
  implementation note.
- **Added, 2026-09-21** (user-requested): a "💾 Save Slides" button
  (originally labeled "Save to SD", renamed same day) next to
  Slideshow. Unlike the preview above, this calls the real
  `build_daily_slideshow()` builder (renders actual JPEGs, writes
  `slideshows`/`slideshow_items`) and gets the result onto whatever
  device the dashboard is open on — a phone or laptop on the LAN, not
  the Pi itself. Went through three revisions the same day, two of
  them dead ends discovered live against the real hardware, before
  landing on the current approach:
  1. First cut: a single ZIP download, for the viewer to extract and
     copy onto a card by hand.
  2. User clarified the actual intent — browse directly to an
     already-connected frame's DCIM folder (or an SD card) and write
     files straight there, no ZIP/extract step. Built via the File
     System Access API's folder picker (Chrome/Edge only, and only
     over a secure context — see `dashboard.tls_cert_path`/
     `tls_key_path`, §18, added specifically for this). Turned out not
     to work for the actual target device: that API can't see or
     target MTP-connected devices (which is how the WF1561, like most
     Android devices, exposes storage over USB) at all — only things
     with a real OS filesystem path. Confirmed live: Explorer could
     browse into the frame fine, Chrome's picker couldn't see it.
  3. Tried downloading every file individually into `Downloads/
     Birdbrain Slideshow/<date>/`, assuming a subfolder path in an
     `<a download>` attribute would make Chrome create that folder.
     Also wrong, confirmed live: Chrome sanitizes the slashes into
     underscores instead of creating folders, and triggering 20
     downloads in a loop tripped a "this site wants to download
     multiple files" permission gate partway through, leaving some
     files stuck pending confirmation the user hadn't been warned to
     expect.
  4. **Current behavior**: back to a single ZIP download (one file, no
     permission gate, no folder-creation assumption to get wrong), now
     with every entry stored under a `<date>/` prefix inside the
     archive so the one extract step the viewer does produces a real
     dated folder directly — the outcome step 3 wanted, reached
     through the mechanism step 1 already had.
  The HTTPS setup from step 2 was left in place (harmless, already
  deployed) even though nothing on this button needs it anymore. See
  DEVELOPMENT.md's dated implementation notes for the full history.
- Frame adapter status
- Last frame delivery result
- Available disk space
- Service health
- Recent errors

### 22.2 Timeline Page

The timeline should support:

- Date filtering
- Species filtering
- Confidence filtering
- Audio playback
- Detection review
- Confirm or reject controls
- Image preview
- Slideshow inclusion status

### 22.3 Species Page

Each species page should show:

- Common name
- Scientific name
- First detected date
- Most recent detection
- Total detection count
- Detection timeline
- Approved images
- Image source and attribution
- Replace-image control

---

## 23. Security and Privacy

### 23.1 Audio Privacy

Raw outdoor audio shall remain local unless the user explicitly enables an external integration.

Audio shall not be uploaded to image providers.

Image searches shall contain species names and optional regional terms only.

### 23.2 Credentials

API keys and frame credentials shall:

- Be stored outside the repository.
- Use restrictive filesystem permissions.
- Never appear in logs.
- Never be embedded in slideshow metadata.
- Be redacted from diagnostic reports.

### 23.3 Network Exposure

The status interface shall bind to localhost by default.

Remote access shall require explicit configuration and authentication.

### 23.4 Device Privacy

The frame serial number, MAC address, local IP address, pairing codes, and account identifiers shall not be committed to Git or included in public documentation.

---

## 24. Suggested Project Structure

```text
backyard-bird-display/
├── README.md
├── CLAUDE.md
├── pyproject.toml
├── requirements.lock
├── .gitignore
├── config/
│   ├── config.example.yaml
│   └── launchd/
│       ├── com.backyardbird.capture.plist
│       ├── com.backyardbird.analyzer.plist
│       └── com.backyardbird.scheduler.plist
├── src/
│   └── backyard_bird/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── logging_config.py
│       ├── audio/
│       │   ├── capture_service.py
│       │   ├── devices.py
│       │   ├── segmenter.py
│       │   └── retention.py
│       ├── analysis/
│       │   ├── worker.py
│       │   ├── birdnet_adapter.py
│       │   ├── result_parser.py
│       │   ├── deduplicator.py
│       │   └── clip_extractor.py
│       ├── database/
│       │   ├── connection.py
│       │   ├── migrations.py
│       │   ├── repositories.py
│       │   └── models.py
│       ├── images/
│       │   ├── service.py
│       │   ├── ranking.py
│       │   ├── validation.py
│       │   ├── cache.py
│       │   └── providers/
│       │       ├── base.py
│       │       ├── wikimedia.py
│       │       └── inaturalist.py
│       ├── slideshow/
│       │   ├── builder.py
│       │   ├── renderer.py
│       │   ├── manifest.py
│       │   └── templates.py
│       ├── frame/
│       │   ├── base.py
│       │   ├── service.py
│       │   └── adapters/
│       │       ├── local_export.py
│       │       ├── removable_media.py
│       │       ├── uhale_web.py
│       │       └── unconfigured.py
│       ├── scheduler/
│       │   ├── service.py
│       │   └── jobs.py
│       └── web/
│           ├── app.py
│           ├── routes.py
│           ├── templates/
│           └── static/
├── migrations/
│   ├── 001_initial_schema.sql
│   └── 002_add_frame_delivery.sql
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── sample_audio/
├── scripts/
│   ├── install.sh
│   ├── uninstall.sh
│   ├── start-dev.sh
│   ├── check-audio-device.sh
│   └── backup-database.sh
└── data/
    ├── audio/
    │   ├── incoming/
    │   ├── processing/
    │   ├── processed/
    │   ├── failed/
    │   └── clips/
    ├── database/
    ├── images/
    ├── slideshows/
    ├── frame-export/
    ├── logs/
    └── temp/
```

The `data` directory shall be excluded from Git except for placeholders where required.

---

## 25. Command-Line Interface

Primary command:

```bash
bird-display
```

Required subcommands:

```bash
bird-display config validate
bird-display audio list-devices
bird-display audio test
bird-display capture run
bird-display analyze run
bird-display analyze file <path>
bird-display queue status
bird-display database migrate
bird-display database integrity-check
bird-display database backup
bird-display detections list
bird-display detections today
bird-display species list
bird-display images fetch-missing
bird-display images refresh <species>
bird-display slideshow build --date YYYY-MM-DD
bird-display slideshow export --date YYYY-MM-DD
bird-display slideshow publish --date YYYY-MM-DD
bird-display frame inspect
bird-display frame test
bird-display services status
bird-display doctor
bird-display setup
```

**Addition (2026-09-14):** `setup` was not in the original list above.
Added once making the project easy to install for other users (e.g.
sharing it publicly, or Michael's use of the Mac build) became an
actual goal: an interactive first-run wizard that geocodes a
city/state/ZIP into `location.latitude/longitude` and lets the user
pick a detected microphone by name, rather than requiring a fresh
install to hand-edit `config.yaml`'s two fields that otherwise fail
*silently* (§10.3's geographic filter quietly excludes real species at
a wrong location; a stale/placeholder device name makes capture retry
forever with no crash — see §31.1's Linux note on ALSA device-index
renumbering for a real instance of the second one). `scripts/install.sh`
offers to run it automatically right after creating `config.yaml` from
the example, for a freshly created config only.

The `doctor` command shall test:

- Python environment
- BirdNET installation
- Audio-device availability
- Microphone permissions
- Database access
- Required directories
- Disk space
- Image-provider configuration
- Frame configuration
- Network access
- `launchd` service definitions

---

## 26. Error Handling

### 26.1 Microphone Failure

- Log the event.
- Mark capture as degraded.
- Retry with increasing delay.
- Avoid crash loops.
- Resume recording when the device returns.
- Record outage duration.

### 26.2 BirdNET Failure

- Increment attempt count.
- Record the full error.
- Retry according to policy.
- Move repeatedly failing files to `failed`.
- Continue with later files.
- Mark analyzer health as degraded.

Recommended maximum attempts:

```text
3
```

### 26.3 Image Search Failure

- Retain the species detection.
- Mark image acquisition as pending.
- Retry later.
- Optionally create a text-only placeholder.
- Preserve provider errors without exposing credentials.

### 26.4 Frame Delivery Failure

- Preserve the last successful frame content.
- Preserve the current export package.
- Record the failed attempt.
- Retry automatically where supported.
- Do not interrupt capture or analysis.

---

## 27. Testing Requirements

### 27.1 Unit Tests

Tests shall cover:

- Configuration validation
- File naming
- Queue transitions
- BirdNET result parsing
- Species normalization
- Duplicate detection
- Database repositories
- Image scoring
- Image validation
- Slideshow ordering
- Manifest generation
- Frame adapter behavior
- Retention policies

### 27.2 Integration Tests

Tests shall cover:

- WAV file through BirdNET to SQLite
- Multiple detections in one segment
- Duplicate detections across overlap
- Clean database migration
- Mocked image-provider responses
- Image download and cache
- 1920 × 1080 slide generation
- Frame export package creation
- Failed delivery retry
- Restart recovery
- Full daily pipeline

### 27.3 Hardware Tests

Acceptance testing shall include:

- Selected outdoor microphone
- 24 hours of continuous capture
- Microphone disconnect and reconnect
- Mac reboot
- Network interruption
- Frame power cycle
- USB or memory-card import
- Uhale Web inspection
- Low-disk simulation
- Analysis backlog
- Quiet audio
- Wind
- Rain
- Human speech
- Mechanical noise
- Multiple simultaneous calls

---

## 28. Acceptance Criteria

Version 1 is complete when:

1. The Mac automatically begins capturing after reboot.
2. The microphone operates continuously for at least 24 hours.
3. Completed audio files are automatically processed by BirdNET.
4. Detections are stored in SQLite with species, timestamp, and confidence.
5. Overlap duplicates are identified.
6. The database returns a unique species list for any day.
7. Images are retrieved automatically for newly detected species.
8. Images and source metadata are cached locally.
9. A 1920 × 1080 daily slideshow is generated automatically.
10. Each qualifying daily species appears once.
11. The slideshow can be delivered to the Euphro frame through the selected adapter or exported in a ready-to-transfer package.
12. A network failure causes no detection-data loss.
13. BirdNET failure does not stop audio capture.
14. Queued work resumes after reboot.
15. Retention rules prevent uncontrolled storage growth.
16. Logs make failures understandable.
17. Configuration changes do not require source edits.
18. Installation can be reproduced from repository documentation.
19. The frame delivery method has been documented and tested on the physical WF1561.
20. No private frame identifiers are stored in source control.

---

## 29. Development Phases

### Phase 1: Environment and BirdNET Validation

Deliverables:

- Repository skeleton
- Python environment
- BirdNET-Analyzer installed on Apple Silicon
- Known WAV analyzed
- Microphone discovered
- Initial performance measurements
- Configuration loader

Exit condition:

> A supplied WAV file produces parsed BirdNET results through the project adapter.

### Phase 2: Continuous Audio Capture

Deliverables:

- Device selection
- Continuous capture
- Segmentation
- Atomic queue files
- Reconnection handling
- Capture logging
- Retention

Exit condition:

> The outdoor microphone records analyzable segments continuously for 24 hours.

### Phase 3: Detection Pipeline and Database

Deliverables:

- Analysis worker
- BirdNET result parser
- SQLite migrations
- Species normalization
- Duplicate suppression
- Queue recovery
- CLI timeline queries

Exit condition:

> Live microphone audio produces a reliable, searchable SQLite timeline.

### Phase 4: Image Acquisition

Deliverables:

- Provider interface
- First provider adapter
- License metadata
- Image validation
- Image ranking
- Local cache
- Missing-image scheduler

Exit condition:

> Every test species has an approved image or a recorded unavailable status.

### Phase 5: Slideshow Generation

Deliverables:

- Daily aggregation — **done, 2026-09-20**: migration 005 adds
  `daily_species_summary`/`slideshows`/`slideshow_items` (§12.5/12.7/
  12.8). `backyard_bird.aggregation.service.aggregate_local_date()`
  recomputes one local day's summary from `detections` in a single
  transaction (prune stale species, then upsert current ones) — a full
  recompute against the authoritative table each time, not an
  incremental accumulator, so it's idempotent and restart-safe (§30
  rules 8/9) by construction. `dates_due_for_aggregation()` implements
  §14's schedule (today every run, plus yesterday for ~20 minutes
  after local midnight to "finalize" it) as a pure decision function;
  no scheduler/CLI command drives it yet. See DEVELOPMENT.md's dated
  implementation note.
- 1920 × 1080 templates, cropping and optimization — cropping/
  optimization was already done (Phase 4, `images/cache.py`'s
  `build_optimized_frame_image`, §15.6). Template rendering itself —
  **done, 2026-09-20**: `backyard_bird.slideshow.renderer.render_slide()`
  takes that already-1920×1080 base image and composites the slide
  frame on top of it (§16.4). See DEVELOPMENT.md's dated implementation
  note.
- Text overlays — **done, 2026-09-20**, same module: name/time/count/
  confidence/attribution text (§16.2), Clean and Informational modes
  (§16.3), safe margins and unobtrusive attribution placement (§16.4).
- Manifest — **done, 2026-09-21**: `backyard_bird.slideshow.builder.
  build_daily_slideshow()` ties aggregation + approved images +
  `render_slide()` + ordering (§16.5) together — refreshes
  `daily_species_summary` for a local date, resolves each qualifying
  species' approved image, renders its slide, and records a real
  `SlideshowManifest` plus `slideshows`/`slideshow_items` rows
  (§12.7/12.8, previously unused since migration 005). See
  DEVELOPMENT.md's dated implementation note.
- Incremental rebuilding — not yet done (`render_slide()` is
  deterministic per-slide and `build_daily_slideshow()` is a full
  rebuild-and-replace each call, both of which a future "only
  re-render what changed" step needs to be correct on top of — but
  nothing makes that decision yet; every build currently re-renders
  every qualifying species).
- Historical storage — **partially done, 2026-09-21**: each build
  writes to its own `data/slideshows/<local_date>/` directory rather
  than overwriting a single "current" location, so calling the builder
  on consecutive days naturally accumulates history. No retention/
  pruning of old dates exists yet (unlike `LocalExportAdapter`'s single
  `current`/`.previous` pair, or `slideshow.retain_daily_slideshows_days`
  in config, which nothing reads yet).

Exit condition:

> A complete daily slideshow is built automatically from SQLite.

Still not met on the "automatically" half — `build_daily_slideshow()`
now produces a complete, real slideshow from SQLite (proven by the
dashboard's "Save Slides" button, §22.1, which calls it directly), but
nothing calls it on a schedule. It only runs when a human triggers it
(that button, or eventually a CLI command) — the daily-aggregation
schedule §14 already specifies (`dates_due_for_aggregation()`,
§29 Phase 5's aggregation deliverable above) is wired up as a decision
function but still has no scheduler/cron driving it, and the same is
true one level up for the builder itself.

### Phase 6: Euphro Frame Investigation and Adapter

Deliverables:

- Physical-frame firmware inventory — **done, 2026-09-20**: Model
  WF1561, Android 8.1, Rockchip RK3326 SoC, "Uhale" firmware (build
  `RK3326_8.1_RM_AIC8800_ZXV4.2.0`, FSTR 5.1.5). Serial number, MAC
  addresses, and Terminal ID were also read from the device's Settings
  screen but are deliberately not recorded here or anywhere else in
  the repo (§23.4/§30 rule 28).
- Uhale Web workflow verification — **done, 2026-09-20**: no "Uhale
  Web" / browser-pairing option is present in this unit's Settings
  menu. This rules out §17.3 delivery priorities 1 (Uhale Web) and 3
  (browser automation of it — there is no web UI on this unit to
  automate). Priority 2 (a documented local-network or cloud API)
  remains unconfirmed and is not being pursued, per §30 rule 24 and
  §17.1's ban on reverse engineering as a v1 requirement.
- External-media import verification — **done, 2026-09-20**: USB/SD
  import works on this unit, confirming §17.3 priority 4 as the
  dependable delivery path going forward. See DEVELOPMENT.md's dated
  implementation note.
- Adapter decision record — **partially done, 2026-09-20**: the
  `PhotoFrameAdapter` interface (§17.4) and its adapter registry now
  exist in code (`src/backyard_bird/frame/`), with `local_export`
  implemented and `unconfigured` as the safe default for anything else
  (including a future `uhale_web`, not yet implemented — the project
  still has no confirmed public/documented Uhale API, per §30 rule 24).
- Initial delivery adapter — **done, 2026-09-20**: `LocalExportAdapter`
  (§17.5), independent of the not-yet-built slideshow generator
  (§29 Phase 5) — it takes a rendered slide directory + manifest and
  packages them, so it's already testable and already the guaranteed
  fallback rule 29 requires.
- Manual fallback export — **done, 2026-09-20**: this *is* the manual
  fallback export (`local_export` writes `data/frame-export/current/`
  with the slides, `manifest.json`, and `README.txt` per §17.5).
- Delivery logging — not yet done (depends on a delivery scheduler,
  §29 Phase 7, that doesn't exist yet to log from).

See DEVELOPMENT.md's dated implementation note for how the adapter
package was built and what testing covers it.

Exit condition:

> A generated slideshow can be transferred reproducibly to the WF1561.

Not yet met — the slideshow builder (§29 Phase 5) that would produce a
real `SlideshowManifest` for `LocalExportAdapter.publish_slideshow()`
to act on doesn't exist yet, so this has only been exercised against
synthetic test fixtures, not a real daily slideshow.

### Phase 7: Unattended Delivery

Deliverables:

- Automated adapter, where feasible
- Connection testing
- Incremental publishing
- Retry behavior
- Delivery health status
- Safe fallback

Exit condition:

> The daily slideshow appears on the frame without manual transfer, or the project documents why the vendor platform prevents safe automation and provides a dependable export workflow.

### Phase 8: Operations

Deliverables:

- `launchd` definitions (macOS) / `systemd` unit definitions (Linux) — **done, 2026-09-14** (`bird-display services install`/`uninstall`/`status`; see `src/backyard_bird/service_install.py` and README's dated implementation note)
- Installation script
- Heartbeats
- `doctor` command
- Database backup
- Optional dashboard
- Recovery testing
- Final documentation

Exit condition:

> The system starts after reboot and operates unattended.

**Implementation note (2026-09-14):** `bird-display services install`
renders and installs one systemd unit (Linux, system-level, under
`/etc/systemd/system/`, needs sudo) or launchd LaunchAgent plist
(macOS, user-level, under `~/Library/LaunchAgents/`, no sudo) per
service, each independently restart-on-failure — one crashing service
does not affect the others (§20.1). The macOS choice is a LaunchAgent,
never a LaunchDaemon, specifically because §31.1 already established
that macOS blocks microphone capture for any process without an
attached GUI session; a LaunchDaemon would hit that wall permanently.
`bird-display doctor` gained a `service_autostart` check (warns, never
fails — the manual `scripts/start_all.sh`/`stop_all.sh` launcher
remains a fully supported alternative, so auto-start is a convenience,
not a hard requirement).

---

## 30. Claude Code Implementation Rules

Claude Code shall follow these rules:

1. Do not place business logic in shell scripts.
2. Keep BirdNET integration behind a dedicated adapter.
3. Keep photo-frame integration behind a dedicated adapter.
4. Keep image providers behind provider classes.
5. Do not embed secrets in source code.
6. Use parameterized SQL.
7. Require a migration for every schema change.
8. Make every queued operation restart-safe.
9. Make scheduled tasks idempotent.
10. Audio capture must not depend on internet access.
11. Image failure must not interrupt detection.
12. Frame failure must not interrupt detection.
13. Use Python type hints.
14. Use structured logging rather than service `print` statements.
15. Add tests with each component.
16. Mock external APIs in automated tests.
17. Do not delete raw audio until processing status is committed.
18. Avoid loading long audio files fully into memory.
19. Default to one BirdNET worker.
20. Document all configuration fields.
21. Treat SQLite as the authoritative record.
22. Preserve image source and license metadata.
23. Prefer supported APIs over scraping.
24. Do not assume Uhale offers a public API.
25. Isolate browser automation, if used, behind the frame adapter.
26. Do not root or modify the frame firmware.
27. Keep OS-specific service code (macOS `launchd` vs. Linux `systemd`) separate from core logic.
28. Never commit the frame serial number, pairing code, MAC address, IP address, or credentials.
29. Preserve a manual export path even after automated delivery is implemented.
30. Measure CPU, memory, queue delay, and analysis throughput before increasing concurrency.

---

## 31. Required Preimplementation Decisions

### 31.1 Microphone

Determine:

- Exact microphone model
- USB versus network connection
- Weatherproofing
- Cable distance
- Gain
- Wind protection
- Surge protection
- Stable device identifier (CoreAudio device name on macOS; ALSA/PortAudio device name on Linux — reconfirm the configured `audio.device_name` on each host, since the same physical microphone can enumerate under a different name per OS/driver)

**Linux note (2026-09-13):** macOS blocks microphone capture entirely
for processes with no attached GUI/WindowServer session, which is why
the Mac mini profile requires a physical-console or Screen Sharing
session for anything touching the mic (see `README.md`'s Microphone
section) — SSH cannot do it. Linux's ALSA/PortAudio stack has no
equivalent restriction: a user in the `audio` group can open the
microphone over a plain SSH session with no GUI involved. This is a
genuine simplification on the Raspberry Pi profile, not a gap — it
removes the "must run at the physical console" constraint entirely for
that host.

### 31.2 Installation Location

Determine:

- Latitude and longitude
- Time zone
- Typical weather exposure
- Distance from traffic and mechanical noise
- Distance from the principal bird habitat

### 31.3 Frame Firmware

Determine from the physical device:

- Exact model displayed in settings
- Firmware version
- Platform name
- Uhale Web support
- Available import options
- Internal storage behavior
- Album behavior
- Whether browser-based upload remains authenticated
- Whether a supported unattended transfer method exists

### 31.4 Image Policy

Determine whether the system may use:

- Any image for private household display
- Only Creative Commons images
- Only images with on-slide attribution
- User-supplied image collections

Recommended initial policy:

> Prefer clearly licensed images and preserve full attribution metadata.

### 31.5 Detection Review Policy

Recommended Version 1 behavior:

- Store detections at or above 0.60 confidence.
- Include detections in the slideshow at or above 0.75 confidence.
- Allow later manual confirmation or rejection.
- Permit stricter rules for rare or geographically improbable species.

---

## 32. Future Expansion

Potential later capabilities:

- Live spectrogram dashboard — **done, 2026-09-18**: the species table
  shows a static PNG spectrogram of each species' `best_recordings`
  clip, and the dashboard's "Listen Live" button now also shows a
  real-time waterfall spectrogram of the ongoing microphone feed while
  it plays (client-side, Web Audio API + `<canvas>`, no server-side
  encoding involved) — see DEVELOPMENT.md's dated implementation
  notes for both.
- Selected-species notifications
- Rare-bird alerts
- Seasonal slideshows
- Monthly summaries
- Weather correlation
- Sunrise and sunset correlation
- BirdWeather integration
- eBird integration
- User verification workflow
- Multiple rotating images per species
- Audio playback
- Audio-and-image slideshow videos
- Multiple listening stations
- Secure remote access
- Species statistics
- First-of-year alerts
- Locally generated daily narration
- Native macOS menu-bar interface
- CSV, JSON, or scientific-format export

---

## 33. Initial Definition of Done

The first usable milestone is:

> The outdoor microphone records audio, BirdNET analyzes it, and the application writes timestamped species detections to SQLite continuously for 24 hours.

The first complete product milestone is:

> The system produces an automatically updated 1920 × 1080 slideshow containing one representative image for every qualifying bird species detected during the current day and transfers it reproducibly to the Euphro WF1561.

---

## 34. Recommended First Claude Code Task

Claude Code should begin with an environment-validation spike.

The spike shall:

1. Create the repository structure.
2. Create the Python virtual environment.
3. Install and validate BirdNET-Analyzer on the M1 Mac mini.
4. Analyze one known test WAV.
5. Capture one 30-second microphone segment.
6. Analyze the captured segment.
7. Parse the BirdNET output into typed Python objects.
8. Insert the result into a temporary SQLite database.
9. Record processing duration, CPU use, and memory use.
10. Document Apple Silicon installation or runtime issues.
11. Create a placeholder 1920 × 1080 JPEG slide.
12. Generate a local Euphro-ready export directory.

No live image search or automated frame-delivery work should begin until the end-to-end local detection path is proven.

---

## 35. Reference Notes

The following sources informed the initial frame assumptions and shall be rechecked against the physical device before implementation:

- Euphro WF1561-U documentation describes a 15.6-inch, 1920 × 1080, 16:9 frame.
- Uhale documentation describes app-based sharing, Uhale Web, Wi-Fi setup, and external-media workflows.
- The physical device label supplied for this project identifies the unit as Euphro model WF1561 with 12 V DC, 2 A input.

The production implementation shall treat the physical frame and its installed firmware as the source of truth.
