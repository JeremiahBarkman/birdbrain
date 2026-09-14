// Polls /api/stats every POLL_INTERVAL_MS and re-renders. Plain
// fetch()+setInterval rather than WebSockets/SSE: detections only
// arrive on a ~30s cadence (one per audio segment), so a few-second
// poll is indistinguishable from true real-time here, with far less
// moving infrastructure.
const POLL_INTERVAL_MS = 5000;
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

function recordingCellHtml(recording, scientificName) {
  if (!recording) return '<span class="no-recording">–</span>';
  const starTitle = recording.is_approved
    ? "Human-approved — click to un-star"
    : "Mark this as a good recording";
  return `
    <div class="recording-cell">
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

function renderSpeciesTable() {
  // The whole tbody gets replaced below (innerHTML) — fine for a
  // static thumbnail, but replacing an <audio> element mid-playback
  // would silently cut off a human review listen. Skip this render
  // entirely while anything is playing; playingAudioElements' pause
  // handler re-triggers this once nothing is, so it's never more than
  // one poll cycle stale.
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
  tbody.innerHTML = visible
    .map(
      (s) => `
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
        <td class="col-recording">${recordingCellHtml(s.recording, s.scientific_name)}</td>
        <td class="col-first-seen">${formatLocalTime(s.first_detected_at_utc)}</td>
        <td class="col-last-seen">${formatLocalTime(s.last_detected_at_utc)}</td>
        <td class="col-seen-for">${formatDuration(s.duration_seen_seconds)}</td>
        <td class="col-actions">${actionsCellHtml(s.scientific_name)}</td>
      </tr>`
    )
    .join("");

  showMoreBtn.hidden = visibleSpeciesCount >= currentSpecies.length;
}

document.getElementById("species-show-more").addEventListener("click", () => {
  visibleSpeciesCount += SPECIES_PAGE_INCREMENT;
  renderSpeciesTable();
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
  if (event.key === "Escape") closeImageModal();
});

poll();
setInterval(poll, POLL_INTERVAL_MS);
