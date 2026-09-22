// Polls /api/stats every POLL_INTERVAL_MS and re-renders. Plain
// fetch()+setInterval rather than WebSockets/SSE: detections only
// arrive on a ~30s cadence (one per audio segment), so a few-second
// poll is indistinguishable from true real-time here, with far less
// moving infrastructure.
const POLL_INTERVAL_MS = 5000;
// Live mic level meter (user request, 2026-09-14) is polled separately
// from — and faster than — the main stats poll: it's a much smaller,
// cheaper payload (a status-file read, not a SQLite query), and a
// meter that only refreshed every 5s wouldn't read as "live."
const MIC_STATUS_POLL_INTERVAL_MS = 1000;
const SPECIES_PAGE_SIZE = 10;
const SPECIES_PAGE_INCREMENT = 20;

// How many rows of the species table are currently unfolded. Lives
// outside renderStats() so it survives the 5s poll re-render instead
// of snapping back to the first page every time fresh data arrives.
let visibleSpeciesCount = SPECIES_PAGE_SIZE;
let currentSpecies = [];

// Whether the species-heatmap view (below) is showing instead of the
// table — module-level for the same reason as visibleSpeciesCount: it
// must survive the 5s auto-refresh rather than resetting every poll.
let heatmapActive = false;

// Which recording <audio> elements are currently playing — see the
// guard at the top of renderSpeciesTable() for why this exists.
const playingAudioElements = new Set();

const confidenceFilterEl = document.getElementById("confidence-filter");
const confidenceValueEl = document.getElementById("confidence-value");
const dateFilterEl = document.getElementById("date-filter");
const todayFilterBtn = document.getElementById("today-filter-btn");
const clearFiltersBtn = document.getElementById("clear-filters-btn");

const DEFAULT_MIN_CONFIDENCE_PERCENT = Number(confidenceFilterEl.value); // 45 — the slider's own floor
// Remembered per-browser (user request: it was resetting on every
// reload) via localStorage — deliberately not sent to the server or
// tied to any account, just this one browser's last-used setting.
// Wrapped in try/catch: storage can throw (private browsing, blocked
// site data), and losing the remembered value is fine, but it must
// never break the filter itself.
const MIN_CONFIDENCE_STORAGE_KEY = "birdbrain.minConfidencePercent";

function loadStoredMinConfidencePercent() {
  try {
    const stored = Number(localStorage.getItem(MIN_CONFIDENCE_STORAGE_KEY));
    // Bounds-checked against the slider's own current min/max rather
    // than trusted outright — a value stored before either bound
    // changed (or corrupted storage) must not silently apply an
    // out-of-range filter.
    if (Number.isFinite(stored) && stored >= Number(confidenceFilterEl.min) && stored <= Number(confidenceFilterEl.max)) {
      return stored;
    }
  } catch {
    // ignore — fall through to the default
  }
  return DEFAULT_MIN_CONFIDENCE_PERCENT;
}

function storeMinConfidencePercent(value) {
  try {
    localStorage.setItem(MIN_CONFIDENCE_STORAGE_KEY, String(value));
  } catch {
    // ignore — this is a convenience, not something to fail the filter over
  }
}

// Filter state driving every poll(). Module-level, same reasoning as
// visibleSpeciesCount: it must survive the 5s auto-refresh, not reset
// on every tick.
let minConfidencePercent = loadStoredMinConfidencePercent();
confidenceFilterEl.value = String(minConfidencePercent);
confidenceValueEl.textContent = `${minConfidencePercent}%`;
let filterDate = ""; // "" = all time; else an input[type=date] YYYY-MM-DD string

function filtersActive() {
  return minConfidencePercent > DEFAULT_MIN_CONFIDENCE_PERCENT || filterDate !== "";
}

// input[type=date] wants (and Date#toISOString would give, but in
// UTC) a local YYYY-MM-DD. en-CA is the one common locale whose
// built-in date format is already that order.
function todayLocalDateString() {
  return new Date().toLocaleDateString("en-CA");
}

function applyFilters() {
  todayFilterBtn.classList.toggle("active", filterDate === todayLocalDateString());
  visibleSpeciesCount = SPECIES_PAGE_SIZE; // new filters -> back to page 1
  poll();
}

confidenceFilterEl.addEventListener("input", () => {
  minConfidencePercent = Number(confidenceFilterEl.value);
  confidenceValueEl.textContent = `${minConfidencePercent}%`;
  storeMinConfidencePercent(minConfidencePercent);
  applyFilters();
});

dateFilterEl.addEventListener("change", () => {
  filterDate = dateFilterEl.value;
  applyFilters();
});

todayFilterBtn.addEventListener("click", () => {
  // Toggle: clicking Today again while it's already selected clears
  // the date filter rather than being a no-op.
  const today = todayLocalDateString();
  filterDate = filterDate === today ? "" : today;
  dateFilterEl.value = filterDate;
  applyFilters();
});

clearFiltersBtn.addEventListener("click", () => {
  minConfidencePercent = DEFAULT_MIN_CONFIDENCE_PERCENT;
  confidenceFilterEl.value = String(DEFAULT_MIN_CONFIDENCE_PERCENT);
  confidenceValueEl.textContent = `${DEFAULT_MIN_CONFIDENCE_PERCENT}%`;
  storeMinConfidencePercent(DEFAULT_MIN_CONFIDENCE_PERCENT);
  filterDate = "";
  dateFilterEl.value = "";
  applyFilters();
});

function formatLocalTime(isoUtc) {
  if (!isoUtc) return "–";
  return new Date(isoUtc).toLocaleString();
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function thumbHtml(imageUrl, name) {
  return imageUrl
    ? `<img src="${imageUrl}" alt="${name}">`
    : '<span class="image-placeholder">🕊️</span>';
}

function formatConfidence(confidence) {
  if (confidence === null || confidence === undefined) return "–";
  return `${(confidence * 100).toFixed(0)}%`;
}

// Cornell Lab's All About Birds field guide, keyed by common name
// (spaces -> underscores, hyphens left as-is). Apostrophes are
// dropped rather than kept: their guide slugs are "Bewicks_Wren" and
// "Coopers_Hawk", not "Bewick's_Wren" — keeping the apostrophe 404s
// and lands on a site search page instead (confirmed live). Good
// coverage for backyard species; an occasional rare/non-North-American
// visitor may still 404.
function birdGuideUrl(commonName) {
  const cleaned = commonName.trim().replace(/['’]/g, "");
  const slug = encodeURIComponent(cleaned.replace(/\s+/g, "_"));
  return `https://www.allaboutbirds.org/guide/${slug}/overview`;
}

function recordingCellHtml(recording, scientificName, commonName) {
  if (!recording) return '<span class="no-recording">–</span>';
  const starTitle = recording.is_approved
    ? "Human-approved — click to un-star"
    : "Mark this as a good recording";
  // A small clickable thumbnail, not the full-size spectrogram inline
  // (too small at row height to read, and stacking the full image
  // above the audio player made every row much taller than before) —
  // click opens #recording-modal with a large version of both.
  const spectrogramIcon = recording.spectrogram_url
    ? `
      <button
        class="spectrogram-icon-btn"
        type="button"
        data-spectrogram-url="${recording.spectrogram_url}"
        data-recording-url="${recording.url}"
        data-title="${commonName} (${scientificName})"
        data-duration-seconds="${recording.duration_seconds ?? ""}"
        data-sample-rate="${recording.sample_rate ?? ""}"
        data-highpass-hz="${recording.highpass_hz ?? 0}"
        data-scientific="${scientificName}"
        title="View spectrogram and play recording"
      ><img class="spectrogram-icon" src="${recording.spectrogram_url}" alt="Spectrogram of ${scientificName}'s call"></button>`
    : "";
  return `
    <div class="recording-cell">
      ${spectrogramIcon}
      <audio controls preload="none" src="${recording.url}"></audio>
      <div class="recording-actions">
        <button
          class="star-btn${recording.is_approved ? " is-approved" : ""}"
          type="button"
          data-scientific="${scientificName}"
          data-approved="${recording.is_approved}"
          title="${starTitle}"
        >★</button>
        <a class="save-btn" href="${recording.download_url}" title="Save recording" aria-label="Save recording">⬇</a>
      </div>
    </div>`;
}

function actionsCellHtml(scientificName) {
  return `
    <div class="row-actions">
      <button
        class="reject-btn"
        type="button"
        data-scientific="${scientificName}"
        title="Reject: hide this species (its detections stay in the database, marked rejected)"
      >🚫</button>
      <button
        class="delete-btn"
        type="button"
        data-scientific="${scientificName}"
        title="Delete permanently: removes every detection and recording for this species — cannot be undone"
      >🗑️</button>
    </div>`;
}

function renderStats(data) {
  document.getElementById("stat-total-species").textContent = data.total_species;
  document.getElementById("stat-total-detections").textContent = data.total_detections;
  document.getElementById("stat-species-today").textContent = data.species_today;
  document.getElementById("stat-detections-today").textContent = data.detections_today;

  const recent = data.most_recent;
  document.getElementById("recent-image-slot").innerHTML = thumbHtml(
    recent && recent.image_url,
    recent ? recent.common_name : ""
  );
  document.getElementById("recent-common-name").textContent = recent
    ? recent.common_name
    : "No detections yet";
  document.getElementById("recent-scientific-name").textContent = recent ? recent.scientific_name : "";
  document.getElementById("recent-meta").textContent = recent
    ? `${formatLocalTime(recent.detected_at_utc)} · confidence ${(recent.confidence * 100).toFixed(0)}%`
    : "";

  currentSpecies = data.species;
  renderSpeciesTable();

  document.getElementById("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function speciesRowHtml(s) {
  return `
      <tr>
        <td><div class="species-thumb">${thumbHtml(s.image_url, s.common_name)}</div></td>
        <td class="col-species">
          <a class="species-link" href="${birdGuideUrl(s.common_name)}" target="_blank" rel="noopener noreferrer">
            <strong>${s.common_name}</strong>
          </a>
          <span class="scientific">${s.scientific_name}</span>
        </td>
        <td class="col-detections">${s.detection_count}</td>
        <td class="col-confidence">${formatConfidence(s.confidence)}</td>
        <td class="col-recording">${recordingCellHtml(s.recording, s.scientific_name, s.common_name)}</td>
        <td class="col-first-seen">${formatLocalTime(s.first_detected_at_utc)}</td>
        <td class="col-last-seen">${formatLocalTime(s.last_detected_at_utc)}</td>
        <td class="col-seen-for">${formatDuration(s.duration_seen_seconds)}</td>
        <td class="col-actions">${actionsCellHtml(s.scientific_name)}</td>
      </tr>`;
}

function renderSpeciesTable() {
  // The whole tbody gets replaced below (innerHTML) — fine for a
  // static thumbnail, but replacing an <audio> element mid-playback
  // would silently cut off a human review listen. Skip this render
  // entirely while anything is playing; playingAudioElements' pause
  // handler re-triggers this once nothing is, so it's never more than
  // one poll cycle stale. "Show more" below deliberately does NOT go
  // through this function, precisely so it isn't blocked by this guard
  // just because some unrelated row happens to be playing.
  if (playingAudioElements.size > 0) return;

  const tbody = document.getElementById("species-table-body");
  const showMoreBtn = document.getElementById("species-show-more");

  if (!currentSpecies.length) {
    tbody.innerHTML = filtersActive()
      ? '<tr><td colspan="9">No species match these filters.</td></tr>'
      : '<tr><td colspan="9">No species detected yet.</td></tr>';
    showMoreBtn.hidden = true;
    return;
  }

  const visible = currentSpecies.slice(0, visibleSpeciesCount);
  tbody.innerHTML = visible.map(speciesRowHtml).join("");
  // heatmapActive is checked here (not just at toggle time) because
  // this function also runs on every 5s poll while the heatmap view is
  // showing — without this, that poll would silently un-hide "See
  // more" behind the heatmap.
  showMoreBtn.hidden = heatmapActive || visibleSpeciesCount >= currentSpecies.length;
}

// Appends only the newly revealed rows rather than calling
// renderSpeciesTable() — a full re-render replaces every row's
// innerHTML, which both fights the playing-audio guard above (a click
// here would silently do nothing while any recording is playing) and,
// even when it didn't, would tear down and recreate the audio element
// that's currently playing. Appending leaves every already-rendered
// row, playing or not, untouched.
document.getElementById("species-show-more").addEventListener("click", () => {
  const previousCount = visibleSpeciesCount;
  visibleSpeciesCount += SPECIES_PAGE_INCREMENT;
  const newlyVisible = currentSpecies.slice(previousCount, visibleSpeciesCount);
  document.getElementById("species-table-body").insertAdjacentHTML(
    "beforeend",
    newlyVisible.map(speciesRowHtml).join("")
  );
  document.getElementById("species-show-more").hidden = visibleSpeciesCount >= currentSpecies.length;
});

// Shared by poll() and pollHeatmap() below — both filter the same
// underlying detections the same way (min_confidence, date), just
// aggregate the result differently server-side.
function currentFilterParams() {
  const params = new URLSearchParams({ min_confidence: (minConfidencePercent / 100).toFixed(2) });
  if (filterDate) params.set("date", filterDate);
  return params;
}

async function poll() {
  try {
    const response = await fetch(`/api/stats?${currentFilterParams()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderStats(await response.json());
    if (heatmapActive) pollHeatmap();
  } catch (err) {
    document.getElementById("last-updated").textContent = "Update failed — retrying…";
    console.error("dashboard poll failed:", err);
  }
}

// Species-activity-by-hour heatmap ("🔥 Heatmap" button, user
// request): an alternate view of the same filtered species table
// above — same min_confidence/date filters (currentFilterParams()),
// just bucketed by /api/heatmap into local hours of day instead of
// shown as a flat list. Toggling swaps which of #species-table-wrap /
// #species-heatmap-wrap is visible; the filters themselves stay put.
const heatmapViewBtn = document.getElementById("heatmap-view-btn");
const speciesTableWrap = document.getElementById("species-table-wrap");
const heatmapWrapEl = document.getElementById("species-heatmap-wrap");

function hexToRgb(hex) {
  const n = parseInt(hex.trim().replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// Read fresh on every render (not cached) so a live OS theme switch
// (prefers-color-scheme) is picked up automatically, the same way the
// CSS variables themselves already are.
function heatmapColorStops() {
  const style = getComputedStyle(document.documentElement);
  return ["--heat-low", "--heat-mid", "--heat-high"].map((name) =>
    hexToRgb(style.getPropertyValue(name))
  );
}

function heatCellColor(value, maxValue, stops) {
  if (!value) return "var(--heat-empty)";
  // Logarithmic scaling, same reasoning as the species table's own
  // confidence-independent counts: a count of 1 shouldn't disappear
  // next to a count of 50.
  const intensity = Math.log1p(value) / Math.log1p(maxValue || 1);
  const position = intensity * (stops.length - 1);
  const startIndex = Math.min(Math.floor(position), stops.length - 2);
  const fraction = position - startIndex;
  const start = stops[startIndex];
  const end = stops[startIndex + 1];
  const rgb = start.map((channel, i) => Math.round(channel + (end[i] - channel) * fraction));
  return `rgb(${rgb.join(", ")})`;
}

function heatmapHourLabel(hour) {
  return String(hour).padStart(2, "0");
}

function renderHeatmap(data) {
  const head = document.getElementById("species-heatmap-head");
  const body = document.getElementById("species-heatmap-body");
  const table = document.getElementById("species-heatmap");
  const emptyEl = document.getElementById("species-heatmap-empty");

  const species = data.species || [];

  if (!species.length) {
    head.replaceChildren();
    body.replaceChildren();
    table.hidden = true;
    emptyEl.textContent = filtersActive() ? "No species match these filters." : "No detections yet.";
    emptyEl.hidden = false;
    return;
  }
  table.hidden = false;
  emptyEl.hidden = true;

  const maxValue = Math.max(...species.flatMap((s) => s.hours), 1);
  const stops = heatmapColorStops();

  head.innerHTML = `<tr><th>Species</th>${Array.from(
    { length: 24 },
    (_, hour) => `<th>${heatmapHourLabel(hour)}</th>`
  ).join("")}</tr>`;

  body.innerHTML = species
    .map((s, index) => {
      const cells = s.hours
        .map((value, hour) => {
          const color = heatCellColor(value, maxValue, stops);
          const tooltip =
            `${s.common_name}\n` +
            `${heatmapHourLabel(hour)}:00–${heatmapHourLabel((hour + 1) % 24)}:00\n` +
            `${value} detection${value === 1 ? "" : "s"}`;
          return `<td class="heat-cell" style="background:${color}" title="${tooltip.replace(/"/g, "&quot;")}" aria-label="${s.common_name}, hour ${hour}, ${value} detections"></td>`;
        })
        .join("");
      return `
        <tr>
          <th class="heatmap-species-name">
            <span class="heatmap-rank">${index + 1}</span>
            <span>${s.common_name}</span>
          </th>
          ${cells}
        </tr>`;
    })
    .join("");
}

async function pollHeatmap() {
  try {
    const response = await fetch(`/api/heatmap?${currentFilterParams()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderHeatmap(await response.json());
  } catch (err) {
    console.error("heatmap poll failed:", err);
  }
}

heatmapViewBtn.addEventListener("click", () => {
  heatmapActive = !heatmapActive;
  heatmapViewBtn.classList.toggle("active", heatmapActive);
  heatmapViewBtn.textContent = heatmapActive ? "📋 Table" : "🔥 Heatmap";
  speciesTableWrap.hidden = heatmapActive;
  heatmapWrapEl.hidden = !heatmapActive;
  document.getElementById("species-show-more").hidden = heatmapActive || visibleSpeciesCount >= currentSpecies.length;
  if (heatmapActive) pollHeatmap();
});

// Image popup: a single delegated listener on <body> rather than
// binding to each <img> — the recent-detection image and every
// species-table thumbnail get replaced wholesale on every poll
// (innerHTML), so per-element listeners would need re-attaching after
// each render; delegation just works against whatever's currently in
// the DOM.
function openImageModal(src, alt) {
  const modalImg = document.getElementById("image-modal-img");
  modalImg.src = src;
  modalImg.alt = alt || "";
  document.getElementById("image-modal").hidden = false;
}

function closeImageModal() {
  document.getElementById("image-modal").hidden = true;
  document.getElementById("image-modal-img").src = "";
}

document.body.addEventListener("click", (event) => {
  const clickedImg = event.target.closest(".bird-image-slot img, .species-thumb img");
  if (clickedImg) {
    openImageModal(clickedImg.src, clickedImg.alt);
  }
});

// Recording playback tracking: play/pause don't bubble (per the HTML
// media spec), so this has to listen on the capture phase to catch
// them via delegation — the alternative, binding per <audio> element,
// would need re-binding after every table re-render same as the image
// modal above.
//
// Scoped to #species-table-body specifically (not just "any <audio>
// tag") — the whole point of playingAudioElements is protecting a
// per-row recording clip from being yanked out mid-playback by the
// tbody's innerHTML replacement below. #live-monitor-audio (Listen
// Live) and #recording-modal-audio (the big spectrogram modal) both
// live outside the table and are never touched by that replacement,
// so tracking them here just froze the whole species table for as
// long as either was playing — confirmed live: a user reported a
// detection ("Barred Owl") that appeared in the "Most Recent
// Detection" card, was superseded there, but didn't show up in the
// table below until they stopped Listen Live.
function isRowRecordingAudio(target) {
  return target.tagName === "AUDIO" && target.closest("#species-table-body") !== null;
}

document.body.addEventListener(
  "play",
  (event) => {
    if (isRowRecordingAudio(event.target)) playingAudioElements.add(event.target);
  },
  true
);
document.body.addEventListener(
  "pause",
  (event) => {
    if (!isRowRecordingAudio(event.target)) return;
    playingAudioElements.delete(event.target);
    if (playingAudioElements.size === 0) renderSpeciesTable(); // catch up on whatever the last poll queued
  },
  true
);

// Spectrogram/recording modal: clicking a row's small spectrogram icon
// opens a large version of that same image plus a full-size audio
// player — the row thumbnail is too small to actually read a call's
// detail at a glance, so it's just an entry point into this bigger
// view rather than trying to be readable itself.
const recordingModal = document.getElementById("recording-modal");
const recordingModalAudio = document.getElementById("recording-modal-audio");
const recordingModalPlayhead = document.getElementById("recording-modal-playhead");
const recordingHighpassSelect = document.getElementById("recording-highpass-select");

// 5 evenly spaced ticks on each axis — enough to read the scale
// without crowding a ~200px-tall chart.
const RECORDING_AXIS_TICK_COUNT = 5;

// Shared by the recording modal (a fixed clip, real sample rate) and
// the live "Listen Live" panel (an open-ended stream, the browser
// AudioContext's own sample rate) — both just need "nyquist -> 0"
// labeled top to bottom, matching the spectrogram's own
// low-frequencies-at-the-bottom convention (audio/spectrogram.py).
function renderFrequencyAxis(axisElementId, sampleRate) {
  const axis = document.getElementById(axisElementId);
  if (!sampleRate) {
    axis.innerHTML = "";
    return;
  }
  const nyquist = sampleRate / 2; // the highest frequency this sample rate can represent
  const labels = [];
  for (let i = 0; i < RECORDING_AXIS_TICK_COUNT; i++) {
    const hz = nyquist - (nyquist / (RECORDING_AXIS_TICK_COUNT - 1)) * i;
    labels.push(hz >= 1000 ? `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 1)}k` : `${Math.round(hz)}`);
  }
  axis.innerHTML = labels.map((label) => `<span>${label}</span>`).join("");
}

function renderTimeAxis(durationSeconds) {
  const axis = document.getElementById("recording-modal-time-axis");
  if (!durationSeconds) {
    axis.innerHTML = "";
    return;
  }
  const labels = [];
  for (let i = 0; i < RECORDING_AXIS_TICK_COUNT; i++) {
    const seconds = (durationSeconds / (RECORDING_AXIS_TICK_COUNT - 1)) * i;
    labels.push(`${seconds.toFixed(seconds < 10 ? 1 : 0)}s`);
  }
  axis.innerHTML = labels.map((label) => `<span>${label}</span>`).join("");
}

// High-pass filtering (user request, modeled on birdnet-go's
// spectrogram viewer): a real Web Audio BiquadFilterNode actually
// filters what you hear on playback, while the visible image is
// re-rendered server-side at the same cutoff (?highpass=<hz> on the
// same spectrogram route — see web/routes.py) so what you see matches
// what you hear. A frequency of 0 is "Off": a highpass filter with a
// 0Hz cutoff has nothing below it to attenuate, so this needs no
// separate bypass/on-off state. The selection is remembered per
// species (user request) — GET/POST /api/species/<name>/recording/
// highpass, persisted in best_recordings.highpass_hz — so reopening a
// species later restores its filter instead of resetting to Off.
let recordingAudioCtx = null;
let recordingHighpassFilter = null;
let currentSpectrogramBaseUrl = "";
let currentRecordingScientificName = "";

// Must run synchronously inside the click handler that opens the
// modal (a real user gesture) — same constraint as the live monitor's
// ensureLiveAudioGraph(), and for the same reason: browsers refuse to
// start an AudioContext outside one.
function ensureRecordingAudioGraph() {
  if (!recordingHighpassFilter) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    recordingAudioCtx = new AudioContextClass();
    const source = recordingAudioCtx.createMediaElementSource(recordingModalAudio);
    recordingHighpassFilter = recordingAudioCtx.createBiquadFilter();
    recordingHighpassFilter.type = "highpass";
    recordingHighpassFilter.frequency.value = 0;
    source.connect(recordingHighpassFilter);
    // Required for the element to still be audible — once
    // createMediaElementSource() is called, its normal output no
    // longer reaches the speakers on its own.
    recordingHighpassFilter.connect(recordingAudioCtx.destination);
  }
  if (recordingAudioCtx.state === "suspended") recordingAudioCtx.resume();
}

function openRecordingModal(spectrogramUrl, recordingUrl, title, durationSeconds, sampleRate, highpassHz, scientificName) {
  ensureRecordingAudioGraph();
  currentSpectrogramBaseUrl = spectrogramUrl;
  currentRecordingScientificName = scientificName;
  const hz = highpassHz || 0;
  recordingHighpassSelect.value = String(hz);
  recordingHighpassFilter.frequency.value = hz;

  document.getElementById("recording-modal-title").textContent = title;
  document.getElementById("recording-modal-spectrogram").src = hz ? `${spectrogramUrl}?highpass=${hz}` : spectrogramUrl;
  recordingModalPlayhead.style.left = "0%";
  recordingModalAudio.src = recordingUrl;
  renderFrequencyAxis("recording-modal-freq-axis", sampleRate);
  renderTimeAxis(durationSeconds);
  recordingModal.hidden = false;
}

function closeRecordingModal() {
  recordingModal.hidden = true;
  // Actually stops playback/downloading, not just hides the dialog —
  // same reasoning as stopLiveMonitor()'s pause+clear src+load below.
  recordingModalAudio.pause();
  recordingModalAudio.removeAttribute("src");
  recordingModalAudio.load();
  document.getElementById("recording-modal-spectrogram").src = "";
}

recordingHighpassSelect.addEventListener("change", async () => {
  const hz = Number(recordingHighpassSelect.value);
  recordingHighpassFilter.frequency.value = hz;
  document.getElementById("recording-modal-spectrogram").src = hz
    ? `${currentSpectrogramBaseUrl}?highpass=${hz}`
    : currentSpectrogramBaseUrl;

  try {
    const response = await fetch(
      `/api/species/${encodeURIComponent(currentRecordingScientificName)}/recording/highpass`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ highpass_hz: hz }),
      }
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (err) {
    console.error("recording highpass persist failed:", err);
  }
});

document.body.addEventListener("click", (event) => {
  const iconBtn = event.target.closest(".spectrogram-icon-btn");
  if (!iconBtn) return;
  openRecordingModal(
    iconBtn.dataset.spectrogramUrl,
    iconBtn.dataset.recordingUrl,
    iconBtn.dataset.title,
    Number(iconBtn.dataset.durationSeconds) || null,
    Number(iconBtn.dataset.sampleRate) || null,
    Number(iconBtn.dataset.highpassHz) || 0,
    iconBtn.dataset.scientific
  );
});

recordingModal.addEventListener("click", (event) => {
  // Clicking the image, the audio player, or the title stays open —
  // only the dark backdrop around them closes it.
  if (event.target.closest(".recording-modal-content")) return;
  closeRecordingModal();
});

document.getElementById("recording-modal-close").addEventListener("click", closeRecordingModal);

// A single persistent element (unlike the per-row <audio>s, this one
// is never replaced by a table re-render), so a direct listener is
// simpler and just as correct as the capture-phase delegation the
// per-row play/pause tracking above needs.
recordingModalAudio.addEventListener("timeupdate", () => {
  const percent = recordingModalAudio.duration
    ? (recordingModalAudio.currentTime / recordingModalAudio.duration) * 100
    : 0;
  recordingModalPlayhead.style.left = `${percent}%`;
});

// Star (human-approval) toggle: optimistic UI update, then reconcile
// with the server. Delegated for the same reason as the image click
// handler above — the buttons get replaced wholesale every render.
document.body.addEventListener("click", async (event) => {
  const starBtn = event.target.closest(".star-btn");
  if (!starBtn) return;

  const scientificName = starBtn.dataset.scientific;
  const nextApproved = starBtn.dataset.approved !== "true";
  starBtn.classList.toggle("is-approved", nextApproved);
  starBtn.dataset.approved = String(nextApproved);

  try {
    const response = await fetch(`/api/species/${encodeURIComponent(scientificName)}/recording/star`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved: nextApproved }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (err) {
    console.error("star toggle failed:", err);
  }
  poll(); // reconcile immediately rather than waiting up to POLL_INTERVAL_MS
});

// Reject/delete ("kill a false detection", user-requested): both
// require an explicit confirm() before touching the server — the two
// messages are deliberately different, since only delete actually
// destroys anything (reject just hides the species; its detections
// stay in the database, marked rejected). Not optimistic like the
// star toggle above: this changes what rows exist at all, so wait for
// the server's response before re-polling rather than guessing.
document.body.addEventListener("click", async (event) => {
  const rejectBtn = event.target.closest(".reject-btn");
  const deleteBtn = event.target.closest(".delete-btn");
  if (!rejectBtn && !deleteBtn) return;

  const scientificName = (rejectBtn || deleteBtn).dataset.scientific;
  const species = currentSpecies.find((s) => s.scientific_name === scientificName);
  const commonName = species ? species.common_name : scientificName;

  if (rejectBtn) {
    const confirmed = confirm(
      `Reject ${commonName}?\n\nIt will disappear from this table. Its detections stay in the ` +
        `database, marked rejected — nothing is deleted.`
    );
    if (!confirmed) return;

    try {
      const response = await fetch(`/api/species/${encodeURIComponent(scientificName)}/reject`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (err) {
      console.error("reject species failed:", err);
      alert(`Could not reject ${commonName} — see the browser console for details.`);
      return;
    }
  } else {
    const confirmed = confirm(
      `Permanently delete ${commonName}?\n\nThis removes every detection and recording for this ` +
        `species from the database. This cannot be undone.`
    );
    if (!confirmed) return;

    try {
      const response = await fetch(`/api/species/${encodeURIComponent(scientificName)}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (err) {
      console.error("delete species failed:", err);
      alert(`Could not delete ${commonName} — see the browser console for details.`);
      return;
    }
  }

  poll(); // reflect the removal immediately rather than waiting up to POLL_INTERVAL_MS
});

document.getElementById("image-modal").addEventListener("click", (event) => {
  if (event.target.id !== "image-modal-img") closeImageModal(); // clicking the photo itself stays open
});

document.getElementById("image-modal-close").addEventListener("click", closeImageModal);

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeImageModal();
  closeRecordingModal();
});

// Live mic status + level meter (user request, 2026-09-14). Polled
// independently of poll()/renderStats() above — see the interval
// constant's comment for why.
function renderMicStatus(data) {
  const dot = document.getElementById("mic-status-dot");
  const text = document.getElementById("mic-status-text");
  const fill = document.getElementById("level-meter-fill");

  dot.classList.remove("is-capturing", "is-error");
  if (data.status === "capturing") {
    dot.classList.add("is-capturing");
    text.textContent = "Capturing";
  } else if (data.status === "error") {
    dot.classList.add("is-error");
    text.textContent = data.error_message ? `Error: ${data.error_message}` : "Error";
  } else if (data.status === "stopped") {
    text.textContent = "Not running";
  } else {
    text.textContent = "No live data";
  }
  // Device name is now the mic-device-select dropdown below, not text
  // here — see fetchMicDevices()/micDeviceSelect. It's populated once
  // at load rather than re-synced from every 1s status poll, so it
  // doesn't fight a user actively choosing a different device.

  const percent = data.peak_percent ?? 0;
  fill.style.width = `${percent}%`;
  fill.classList.remove("is-loud", "is-clipping");
  if (percent >= 90) fill.classList.add("is-clipping");
  else if (percent >= 70) fill.classList.add("is-loud");
}

async function pollMicStatus() {
  try {
    const response = await fetch("/api/mic-status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderMicStatus(await response.json());
  } catch (err) {
    console.error("mic status poll failed:", err);
  }
}

// "Listen Live" (user request, 2026-09-14): toggles playback of the
// real live audio stream at /api/monitor/live. Stopping does more
// than pause() — clearing src and calling load() actually aborts the
// underlying connection, which is what tells capture_service.py's
// relay this client is gone (see live_monitor.py) rather than leaving
// a phantom listener it keeps broadcasting to.
const liveMonitorBtn = document.getElementById("live-monitor-btn");
const liveMonitorAudio = document.getElementById("live-monitor-audio");
let liveMonitorActive = false;

// Live spectrogram (user request): a real-time waterfall view of the
// mic feed while "Listen Live" plays, drawn with the Web Audio API's
// AnalyserNode straight onto a <canvas> — entirely client-side, no
// server round trip, reusing the same <audio> element Listen Live
// already streams into. The AudioContext/analyser graph is built once
// and kept alive across stop/start cycles (rather than torn down and
// rebuilt) because a media element can only ever be passed to
// createMediaElementSource() once in its lifetime.
const liveSpectrogramPanel = document.getElementById("live-spectrogram-panel");
const liveSpectrogramCanvas = document.getElementById("live-spectrogram-canvas");
const liveHighpassSelect = document.getElementById("live-highpass-select");
const liveZoomSelect = document.getElementById("live-zoom-select");
let liveAudioCtx = null;
let liveAnalyser = null;
let liveHighpassFilter = null;
let liveSpectrogramRAF = null;
const LIVE_SPECTROGRAM_BASE_HEIGHT_PX = 120; // the 1x size; 2x/3x scale this directly

// The same 5-stop magma-like gradient audio/spectrogram.py uses for
// the per-species PNGs, reimplemented here in JS so the live view and
// the static ones read as the same visual language.
const LIVE_SPECTROGRAM_COLOR_STOPS = [
  [0.0, 0, 0, 4],
  [0.25, 81, 18, 124],
  [0.5, 183, 55, 121],
  [0.75, 252, 137, 97],
  [1.0, 252, 253, 191],
];

function liveSpectrogramColor(value) {
  for (let i = 1; i < LIVE_SPECTROGRAM_COLOR_STOPS.length; i++) {
    const [p0, r0, g0, b0] = LIVE_SPECTROGRAM_COLOR_STOPS[i - 1];
    const [p1, r1, g1, b1] = LIVE_SPECTROGRAM_COLOR_STOPS[i];
    if (value <= p1 || i === LIVE_SPECTROGRAM_COLOR_STOPS.length - 1) {
      const t = p1 === p0 ? 0 : (value - p0) / (p1 - p0);
      return `rgb(${Math.round(r0 + (r1 - r0) * t)}, ${Math.round(g0 + (g1 - g0) * t)}, ${Math.round(b0 + (b1 - b0) * t)})`;
    }
  }
  return "rgb(0, 0, 4)";
}

// Must be called synchronously from within the button's click handler
// (not from inside play()'s .then()) — browsers only allow creating an
// AudioContext during an actual user-gesture callback.
function ensureLiveAudioGraph() {
  if (!liveAnalyser) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    liveAudioCtx = new AudioContextClass();
    const source = liveAudioCtx.createMediaElementSource(liveMonitorAudio);
    // High-pass filtering (user request): inserted *before* the
    // analyser, not just in parallel with it — so unlike the static
    // per-clip spectrogram (a pre-rendered PNG that needs a separate
    // server request to reflect a filter change), the live waterfall
    // automatically shows whatever the filter actually leaves behind,
    // for free, just by reading from the same graph a real one flows
    // through. A frequency of 0 is "Off": nothing below it to
    // attenuate, so no separate bypass state is needed.
    liveHighpassFilter = liveAudioCtx.createBiquadFilter();
    liveHighpassFilter.type = "highpass";
    liveHighpassFilter.frequency.value = 0;
    liveAnalyser = liveAudioCtx.createAnalyser();
    liveAnalyser.fftSize = 2048;
    liveAnalyser.minDecibels = -90;
    liveAnalyser.maxDecibels = -10;
    source.connect(liveHighpassFilter);
    liveHighpassFilter.connect(liveAnalyser);
    // Required for the element to still be audible — once
    // createMediaElementSource() is called, its normal output no
    // longer reaches the speakers on its own.
    liveAnalyser.connect(liveAudioCtx.destination);
  }
  if (liveAudioCtx.state === "suspended") liveAudioCtx.resume();
}

// Resets the canvas's internal pixel buffer to match its current
// on-screen size (accounting for both the 1x/2x/3x zoom, which
// changes its CSS height, and devicePixelRatio, for a sharp image on
// retina displays) — shared by the initial start and every zoom
// change, both of which need a freshly cleared buffer at the new size
// rather than whatever stretched/cropped leftover content a plain CSS
// resize alone would show.
function resizeLiveSpectrogramCanvasBuffer() {
  const rect = liveSpectrogramCanvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  liveSpectrogramCanvas.width = Math.max(1, Math.round(rect.width * dpr));
  liveSpectrogramCanvas.height = Math.max(1, Math.round(rect.height * dpr));

  const ctx = liveSpectrogramCanvas.getContext("2d");
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, liveSpectrogramCanvas.width, liveSpectrogramCanvas.height);
}

function applyLiveSpectrogramZoom() {
  const zoom = Number(liveZoomSelect.value) || 1;
  liveSpectrogramCanvas.style.height = `${LIVE_SPECTROGRAM_BASE_HEIGHT_PX * zoom}px`;
  resizeLiveSpectrogramCanvasBuffer(); // changing size invalidates whatever was already drawn anyway
}

function startLiveSpectrogram() {
  if (!liveAnalyser) return; // ensureLiveAudioGraph() wasn't called, or creating it failed
  liveSpectrogramPanel.hidden = false;
  applyLiveSpectrogramZoom();
  renderFrequencyAxis("live-freq-axis", liveAudioCtx.sampleRate);

  const ctx = liveSpectrogramCanvas.getContext("2d");
  const freqData = new Uint8Array(liveAnalyser.frequencyBinCount);

  function draw() {
    liveAnalyser.getByteFrequencyData(freqData);
    const width = liveSpectrogramCanvas.width;
    const height = liveSpectrogramCanvas.height;
    // Scrolls the whole image one column left, then paints one fresh
    // column of bins along the right edge — a waterfall, one column
    // per animation frame, versus the static PNGs rendering every
    // column of a whole clip at once.
    ctx.drawImage(liveSpectrogramCanvas, -1, 0);
    for (let y = 0; y < height; y++) {
      // Low frequencies at the bottom, same convention the static
      // per-species spectrograms use.
      const binIndex = Math.floor(((height - 1 - y) / height) * freqData.length);
      ctx.fillStyle = liveSpectrogramColor(freqData[binIndex] / 255);
      ctx.fillRect(width - 1, y, 1, 1);
    }
    liveSpectrogramRAF = requestAnimationFrame(draw);
  }
  draw();
}

function stopLiveSpectrogram() {
  if (liveSpectrogramRAF) cancelAnimationFrame(liveSpectrogramRAF);
  liveSpectrogramRAF = null;
  // Folded away entirely while inactive (user request) — not just an
  // empty canvas, but the whole panel including the highpass/zoom
  // controls, so none of it is visible or takes up space until
  // "Listen Live" is actually running.
  liveSpectrogramPanel.hidden = true;
}

liveHighpassSelect.addEventListener("change", () => {
  if (liveHighpassFilter) liveHighpassFilter.frequency.value = Number(liveHighpassSelect.value);
});

liveZoomSelect.addEventListener("change", () => {
  if (!liveSpectrogramPanel.hidden) applyLiveSpectrogramZoom();
});

function stopLiveMonitor() {
  liveMonitorAudio.pause();
  liveMonitorAudio.removeAttribute("src");
  liveMonitorAudio.load();
  liveMonitorActive = false;
  liveMonitorBtn.textContent = "🔊 Listen Live";
  liveMonitorBtn.classList.remove("active");
  stopLiveSpectrogram();
}

liveMonitorBtn.addEventListener("click", () => {
  if (liveMonitorActive) {
    stopLiveMonitor();
    return;
  }
  ensureLiveAudioGraph();
  liveMonitorAudio.src = "/api/monitor/live";
  liveMonitorAudio
    .play()
    .then(() => startLiveSpectrogram())
    .catch((err) => {
      console.error("live monitor playback failed:", err);
      alert("Could not start the live audio monitor — see the browser console for details.");
      stopLiveMonitor();
    });
  liveMonitorActive = true;
  liveMonitorBtn.textContent = "⏹ Stop Listening";
  liveMonitorBtn.classList.add("active");
});

liveMonitorAudio.addEventListener("error", () => {
  if (!liveMonitorActive) return; // expected right after we clear src ourselves on stop
  console.error("live monitor stream error");
  stopLiveMonitor();
});

// Software gain control (user request: the mic runs quite low by
// default, with no hardware knob for it). GET/POST /api/mic-gain — see
// audio/gain.py for how a change here reaches the already-running
// capture process live, without a restart.
const gainSlider = document.getElementById("gain-slider");
const gainValueEl = document.getElementById("gain-value");

function renderGainValue(gain) {
  gainValueEl.textContent = `${Number(gain).toFixed(2)}×`;
}

async function fetchGain() {
  try {
    const response = await fetch("/api/mic-gain");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    gainSlider.min = data.min;
    gainSlider.max = data.max;
    gainSlider.value = data.gain;
    renderGainValue(data.gain);
  } catch (err) {
    console.error("gain fetch failed:", err);
  }
}

gainSlider.addEventListener("input", () => renderGainValue(gainSlider.value));

// "change" (fires on release/blur), not "input" (fires continuously
// while dragging) — sends one request per adjustment instead of
// flooding the server while the slider is being dragged.
gainSlider.addEventListener("change", async () => {
  try {
    const response = await fetch("/api/mic-gain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gain: Number(gainSlider.value) }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    gainSlider.value = data.gain; // reflect any server-side clamping
    renderGainValue(data.gain);
  } catch (err) {
    console.error("gain update failed:", err);
  }
});

// Mic device selection (user request: "in case there is more than one
// mic"). Populated once at load from /api/mic-devices, not re-synced
// on every mic-status poll — that would fight a user actively working
// the dropdown. Selecting a different device POSTs the choice, which
// reaches the already-running capture process within about 5 seconds
// (audio/device_control.py's poll interval), no restart needed, and
// is also persisted into config.yaml so a later full restart keeps
// using it.
const micDeviceSelect = document.getElementById("mic-device-select");

async function fetchMicDevices() {
  try {
    const response = await fetch("/api/mic-devices");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();

    let optionsHtml = data.devices.map((d) => `<option value="${d.name}">${d.name}</option>`).join("");
    // The configured/selected device might not be one PortAudio
    // currently sees (unplugged, renamed after a reboot) — list it
    // anyway rather than letting the dropdown silently jump to
    // whatever the first available device happens to be.
    if (data.current && !data.devices.some((d) => d.name === data.current)) {
      optionsHtml = `<option value="${data.current}">${data.current} (not detected)</option>${optionsHtml}`;
    }
    micDeviceSelect.innerHTML = optionsHtml || '<option value="">No input devices found</option>';
    micDeviceSelect.value = data.current || "";
  } catch (err) {
    console.error("mic device list fetch failed:", err);
    micDeviceSelect.innerHTML = '<option value="">Could not load devices</option>';
  }
}

micDeviceSelect.addEventListener("change", async () => {
  const deviceName = micDeviceSelect.value;
  try {
    const response = await fetch("/api/mic-device", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_name: deviceName }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (err) {
    console.error("mic device switch failed:", err);
    alert("Could not switch microphone — see the browser console for details.");
  }
});

poll();
setInterval(poll, POLL_INTERVAL_MS);
pollMicStatus();
setInterval(pollMicStatus, MIC_STATUS_POLL_INTERVAL_MS);
fetchGain();
fetchMicDevices();

// Fullscreen slideshow preview (§16, user request): a browser-side
// companion to the eventual physical-frame delivery pipeline (§29
// Phase 5/6/7) — not that pipeline itself. /api/slideshow applies the
// same qualification rule §16.2 describes (confidence threshold +
// approved image) and returns slides already ordered per
// config.slideshow.order; this just cycles through them fullscreen
// until any key press or mouse action ends it.
const slideshowBtn = document.getElementById("slideshow-btn");
const slideshowOverlay = document.getElementById("slideshow-overlay");
const slideshowImageEl = document.getElementById("slideshow-image");
const slideshowCommonNameEl = document.getElementById("slideshow-common-name");
const slideshowScientificNameEl = document.getElementById("slideshow-scientific-name");
const slideshowInfoEl = document.getElementById("slideshow-info");
const slideshowAttributionEl = document.getElementById("slideshow-attribution");
const slideshowEmptyEl = document.getElementById("slideshow-empty");

let slideshowSlides = [];
let slideshowIndex = 0;
let slideshowTimer = null;
let slideshowDisplayMode = "informational";
let slideshowIntervalMs = 20000;
// Guards against the click that opened the slideshow (and any residual
// mousemove jitter right after) immediately tripping the "exit on
// mouse action" listener before the user has actually looked at it.
let slideshowExitArmedAt = 0;

function slideshowFormatTime(isoString) {
  try {
    return new Date(isoString).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch {
    return "";
  }
}

function renderSlideshowSlide(slide) {
  slideshowImageEl.src = slide.image_url;
  slideshowImageEl.alt = slide.common_name;
  slideshowCommonNameEl.textContent = slide.common_name;
  slideshowScientificNameEl.textContent = slide.scientific_name;

  if (slideshowDisplayMode === "clean") {
    slideshowInfoEl.hidden = true;
    slideshowAttributionEl.hidden = true;
    return;
  }

  const times = slide.detection_count === 1 ? "time" : "times";
  slideshowInfoEl.textContent =
    `First heard: ${slideshowFormatTime(slide.first_detected_local_iso)}` +
    `  •  Detected ${slide.detection_count} ${times} today`;
  slideshowInfoEl.hidden = false;

  slideshowAttributionEl.textContent = slide.attribution_text || "";
  slideshowAttributionEl.hidden = !slide.attribution_text;
}

function advanceSlideshow() {
  if (slideshowSlides.length === 0) return;
  slideshowIndex = (slideshowIndex + 1) % slideshowSlides.length;
  renderSlideshowSlide(slideshowSlides[slideshowIndex]);
}

function stopSlideshow() {
  if (slideshowTimer) {
    clearInterval(slideshowTimer);
    slideshowTimer = null;
  }
  slideshowOverlay.hidden = true;
  slideshowImageEl.src = "";
  document.removeEventListener("keydown", stopSlideshow);
  document.removeEventListener("mousedown", stopSlideshow);
  document.removeEventListener("mousemove", slideshowMaybeStopOnMove);
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
}

function slideshowMaybeStopOnMove() {
  if (Date.now() < slideshowExitArmedAt) return;
  stopSlideshow();
}

async function startSlideshow() {
  let data;
  try {
    const response = await fetch("/api/slideshow");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (err) {
    console.error("slideshow fetch failed:", err);
    alert("Could not load the slideshow — see the browser console for details.");
    return;
  }

  slideshowSlides = data.slides || [];
  slideshowDisplayMode = data.display_mode || "informational";
  slideshowIntervalMs = Math.max(3, Number(data.image_duration_seconds) || 20) * 1000;
  slideshowIndex = 0;

  slideshowEmptyEl.hidden = slideshowSlides.length > 0;
  if (slideshowSlides.length > 0) {
    renderSlideshowSlide(slideshowSlides[0]);
  } else {
    slideshowImageEl.src = "";
    slideshowCommonNameEl.textContent = "";
    slideshowScientificNameEl.textContent = "";
    slideshowInfoEl.hidden = true;
    slideshowAttributionEl.hidden = true;
  }

  slideshowOverlay.hidden = false;
  // Fullscreen can be denied (e.g. no direct user-gesture chain in
  // some embedded contexts) — the overlay still covers the viewport
  // via CSS (position: fixed; inset: 0), so it degrades to a
  // full-page view rather than failing outright.
  slideshowOverlay.requestFullscreen?.().catch(() => {});

  if (slideshowSlides.length > 1) {
    slideshowTimer = setInterval(advanceSlideshow, slideshowIntervalMs);
  }

  slideshowExitArmedAt = Date.now() + 500;
  setTimeout(() => {
    document.addEventListener("keydown", stopSlideshow);
    document.addEventListener("mousedown", stopSlideshow);
    document.addEventListener("mousemove", slideshowMaybeStopOnMove);
  }, 0);
}

slideshowBtn.addEventListener("click", startSlideshow);

// Covers the browser's own fullscreen-exit affordances (Esc, OS/browser
// chrome) in case those don't also deliver a keydown/mousedown the
// listeners above would otherwise catch. Idempotent with stopSlideshow
// above — either path running first leaves the same end state.
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && !slideshowOverlay.hidden) {
    stopSlideshow();
  }
});

// "Save to SD" (user request): builds today's slideshow server-side
// (backyard_bird.slideshow.builder — real rendered JPEGs, not the
// preview's live query) and downloads it as a single ZIP to whatever
// device this browser is running on — a phone or laptop on the LAN,
// not the Pi itself. Extracting it (one right-click) produces a
// folder named for today's date, ready to copy onto an SD card or
// into a connected frame's DCIM folder with the viewer's own file
// manager.
//
// One ZIP, deliberately: two earlier approaches both hit real
// platform limits — the File System Access API's folder picker can't
// target MTP-connected devices (how the actual frame exposes storage
// over USB) at all, and downloading each file individually with a
// subfolder path in its `download` attribute turned out not to create
// real folders in Chrome (slashes get sanitized into underscores) and
// tripped a disruptive "this site wants to download multiple files"
// prompt partway through. A single download avoids both — nothing
// here needs a filesystem handle to anything but Downloads, and one
// file never triggers the multi-download gate.
const saveSdBtn = document.getElementById("save-sd-btn");

saveSdBtn.addEventListener("click", async () => {
  const originalText = saveSdBtn.textContent;
  saveSdBtn.disabled = true;
  saveSdBtn.textContent = "Saving…";
  try {
    const response = await fetch("/api/slideshow/export.zip");
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : "birdbrain-slideshow.zip";

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    console.error("slideshow export failed:", err);
    alert(`Could not save today's slideshow: ${err.message}`);
  } finally {
    saveSdBtn.disabled = false;
    saveSdBtn.textContent = originalText;
  }
});
