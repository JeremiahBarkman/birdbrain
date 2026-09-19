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

// Which recording <audio> elements are currently playing — see the
// guard at the top of renderSpeciesTable() for why this exists.
const playingAudioElements = new Set();

const confidenceFilterEl = document.getElementById("confidence-filter");
const confidenceValueEl = document.getElementById("confidence-value");
const dateFilterEl = document.getElementById("date-filter");
const todayFilterBtn = document.getElementById("today-filter-btn");
const clearFiltersBtn = document.getElementById("clear-filters-btn");

const DEFAULT_MIN_CONFIDENCE_PERCENT = Number(confidenceFilterEl.value); // 45 — the slider's own floor
// Filter state driving every poll(). Module-level, same reasoning as
// visibleSpeciesCount: it must survive the 5s auto-refresh, not reset
// on every tick.
let minConfidencePercent = DEFAULT_MIN_CONFIDENCE_PERCENT;
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
  showMoreBtn.hidden = visibleSpeciesCount >= currentSpecies.length;
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

async function poll() {
  try {
    const params = new URLSearchParams({ min_confidence: (minConfidencePercent / 100).toFixed(2) });
    if (filterDate) params.set("date", filterDate);
    const response = await fetch(`/api/stats?${params}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderStats(await response.json());
  } catch (err) {
    document.getElementById("last-updated").textContent = "Update failed — retrying…";
    console.error("dashboard poll failed:", err);
  }
}

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
document.body.addEventListener(
  "play",
  (event) => {
    if (event.target.tagName === "AUDIO") playingAudioElements.add(event.target);
  },
  true
);
document.body.addEventListener(
  "pause",
  (event) => {
    if (event.target.tagName !== "AUDIO") return;
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

function openRecordingModal(spectrogramUrl, recordingUrl, title) {
  document.getElementById("recording-modal-title").textContent = title;
  document.getElementById("recording-modal-spectrogram").src = spectrogramUrl;
  recordingModalPlayhead.style.left = "0%";
  recordingModalAudio.src = recordingUrl;
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

document.body.addEventListener("click", (event) => {
  const iconBtn = event.target.closest(".spectrogram-icon-btn");
  if (!iconBtn) return;
  openRecordingModal(iconBtn.dataset.spectrogramUrl, iconBtn.dataset.recordingUrl, iconBtn.dataset.title);
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
const liveSpectrogramCanvas = document.getElementById("live-spectrogram-canvas");
let liveAudioCtx = null;
let liveAnalyser = null;
let liveSpectrogramRAF = null;

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
    liveAnalyser = liveAudioCtx.createAnalyser();
    liveAnalyser.fftSize = 2048;
    liveAnalyser.minDecibels = -90;
    liveAnalyser.maxDecibels = -10;
    source.connect(liveAnalyser);
    // Required for the element to still be audible — once
    // createMediaElementSource() is called, its normal output no
    // longer reaches the speakers on its own.
    liveAnalyser.connect(liveAudioCtx.destination);
  }
  if (liveAudioCtx.state === "suspended") liveAudioCtx.resume();
}

function startLiveSpectrogram() {
  if (!liveAnalyser) return; // ensureLiveAudioGraph() wasn't called, or creating it failed
  liveSpectrogramCanvas.hidden = false;

  const rect = liveSpectrogramCanvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  liveSpectrogramCanvas.width = Math.max(1, Math.round(rect.width * dpr));
  liveSpectrogramCanvas.height = Math.max(1, Math.round(rect.height * dpr));

  const ctx = liveSpectrogramCanvas.getContext("2d");
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, liveSpectrogramCanvas.width, liveSpectrogramCanvas.height);

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
  liveSpectrogramCanvas.hidden = true;
}

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
