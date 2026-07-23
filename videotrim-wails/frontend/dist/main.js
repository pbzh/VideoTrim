// VideoTrim — Wails frontend
// Go methods are available via window.go.main.App.*

const go = window.go.main.App;

// --- State ---
let videoDurationMs = 0;
let frameDurationMs = 33;  // default ~30 fps, updated on video load
let sliderDragging = false;
let lastInfoDurationMs = 0; // fallback from ffprobe when video.duration is unreliable
let encoders = [];          // [{label, encoder, hint}]

// --- DOM ---
const el = id => document.getElementById(id);
const video         = el('video-preview');
const filePath      = el('file-path');
const infoLabel     = el('info-label');
const playBtn       = el('play-btn');
const stepBack1s    = el('step-back-1s');
const stepBackFrame = el('step-back-frame');
const stepFwdFrame  = el('step-fwd-frame');
const stepFwd1s     = el('step-fwd-1s');
const posLabel      = el('position-label');
const scrubSlider   = el('scrub-slider');
const totalLabel    = el('total-label');
const startTimeIn   = el('start-time');
const endTimeIn     = el('end-time');
const setStartBtn   = el('set-start-btn');
const setEndBtn     = el('set-end-btn');
const detectStartBtn = el('detect-start-btn');
const durationLabel = el('duration-label');
const outputBrowseBtn = el('output-browse-btn');
const overwriteChk  = el('overwrite-source');
const encodingCombo = el('encoding-combo');
const encodingHint  = el('encoding-hint');
const outputPath    = el('output-path');
const trimBtn       = el('trim-btn');
const progressBox   = el('progress-container');
const statusLabel   = el('status-label');
const placeholder   = el('video-placeholder');

// --- Time utilities ---

function msToTime(ms) {
  ms = Math.max(0, Math.round(ms));
  const h    = Math.floor(ms / 3600000);
  const m    = Math.floor((ms % 3600000) / 60000);
  const s    = Math.floor((ms % 60000) / 1000);
  const msec = ms % 1000;
  return `${p2(h)}:${p2(m)}:${p2(s)}.${p3(msec)}`;
}

function timeToMs(str) {
  const match = str.trim().match(/^(\d+):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?$/);
  if (!match) return -1;
  const msec = match[4] ? parseInt(match[4].padEnd(3, '0')) : 0;
  return parseInt(match[1]) * 3600000
       + parseInt(match[2]) * 60000
       + parseInt(match[3]) * 1000
       + msec;
}

function p2(n) { return String(n).padStart(2, '0'); }
function p3(n) { return String(n).padStart(3, '0'); }

// --- Enable / disable playback controls ---

function setControlsEnabled(on) {
  playBtn.disabled       = !on;
  stepBack1s.disabled    = !on;
  stepBackFrame.disabled = !on;
  stepFwdFrame.disabled  = !on;
  stepFwd1s.disabled     = !on;
  setStartBtn.disabled   = !on;
  setEndBtn.disabled     = !on;
  detectStartBtn.disabled = !on;
  trimBtn.disabled       = !on;
  scrubSlider.disabled   = !on;
}

// --- Initialise encoder list ---

async function initEncoders() {
  encoders = await go.GetAvailableEncoders() || [];

  encodingCombo.innerHTML = '';

  const smartOpt = document.createElement('option');
  smartOpt.value = 'smart';
  smartOpt.textContent = 'Smart (recommended — lossless when possible)';
  encodingCombo.appendChild(smartOpt);

  const copyOpt = document.createElement('option');
  copyOpt.value = 'copy';
  copyOpt.textContent = 'Stream Copy (fast, no re-encoding)';
  encodingCombo.appendChild(copyOpt);

  for (const enc of encoders) {
    const opt = document.createElement('option');
    opt.value = enc.encoder;
    opt.textContent = enc.label;
    encodingCombo.appendChild(opt);
  }

  updateEncodingHint();
}

function updateEncodingHint() {
  const val = encodingCombo.value;
  if (val === 'smart') {
    encodingHint.textContent = 'Lossless stream-copy when start is on a keyframe; otherwise a near-lossless hardware re-encode for a frame-accurate cut';
    return;
  }
  if (val === 'copy') {
    encodingHint.textContent = 'Fastest — cuts on nearest keyframe, no quality loss';
    return;
  }
  const enc = encoders.find(e => e.encoder === val);
  encodingHint.textContent = enc ? enc.hint : '';
}

encodingCombo.addEventListener('change', updateEncodingHint);

// --- Browse input ---

el('browse-btn').addEventListener('click', async () => {
  const path = await go.OpenVideoFile();
  if (path) await loadVideo(path);
});

// --- Load video ---

async function loadVideo(path) {
  filePath.value = path;
  statusLabel.textContent = '';
  statusLabel.className = 'status-label';

  infoLabel.textContent = 'Loading…';
  const info = await go.GetVideoInfo(path);

  if (info.error) {
    infoLabel.textContent = 'Error: ' + info.error;
    infoLabel.style.color = 'var(--error)';
    setControlsEnabled(false);
    return;
  }

  infoLabel.style.color = '';
  const audioInfo = info.audioCodec ? ` | Audio: ${info.audioCodec}` : ' | No audio';
  infoLabel.textContent = `Format: ${info.formatName} | Video: ${info.videoCodec}${audioInfo}`;

  if (info.fps > 0) {
    frameDurationMs = Math.round(1000 / info.fps);
  }
  lastInfoDurationMs = info.durationMs || 0;

  // Point <video> at the Go file-server endpoint (cache-bust so a replaced
  // source reloads its new content instead of the stale cached video)
  video.src = `/video?path=${encodeURIComponent(path)}&_=${Date.now()}`;
  video.classList.add('loaded');
  placeholder.style.display = 'none';

  // Auto-generate output path
  const auto = await go.AutoOutputPath(path);
  outputPath.value = auto;

  setControlsEnabled(true);
}

// --- Video element events ---

video.addEventListener('loadedmetadata', () => {
  if (isFinite(video.duration) && video.duration > 0) {
    videoDurationMs = Math.floor(video.duration * 1000);
  } else {
    videoDurationMs = lastInfoDurationMs;
  }

  scrubSlider.max = videoDurationMs;
  totalLabel.textContent = msToTime(videoDurationMs);

  startTimeIn.value = '00:00:00.000';
  endTimeIn.value   = msToTime(videoDurationMs);
  validateTimeInputs();
  updateDurationLabel();
});

video.addEventListener('timeupdate', () => {
  if (sliderDragging) return;
  const ms = Math.floor(video.currentTime * 1000);
  posLabel.textContent = msToTime(ms);
  scrubSlider.value = ms;
});

video.addEventListener('play',  () => { playBtn.textContent = '\u23F8'; });  // ⏸
video.addEventListener('pause', () => { playBtn.innerHTML = '&#9654;'; });   // ▶
video.addEventListener('ended', () => { playBtn.innerHTML = '&#9654;'; });

video.addEventListener('error', () => {
  const codes = {
    1: 'Playback aborted',
    2: 'Network error loading video',
    3: 'Video decode failed',
    4: 'Format or codec not supported by the preview (WebView2 supports MP4/H.264, WebM/VP9/AV1; MKV/AVI/WMV/HEVC will not preview). Trimming still works.',
  };
  const err = video.error;
  const msg = err ? (codes[err.code] || `Video error code ${err.code}`) : 'Unknown video error';
  showStatus('Preview: ' + msg, 'error');
});

// --- Playback controls ---

playBtn.addEventListener('click', () => {
  video.paused ? video.play() : video.pause();
});

stepBack1s.addEventListener('click',    () => seekByMs(-1000));
stepFwd1s.addEventListener('click',     () => seekByMs(1000));
stepBackFrame.addEventListener('click', () => seekByMs(-frameDurationMs));
stepFwdFrame.addEventListener('click',  () => seekByMs(frameDurationMs));

function seekByMs(delta) {
  video.pause();
  const newMs = Math.max(0, Math.min(Math.floor(video.currentTime * 1000) + delta, videoDurationMs));
  video.currentTime = newMs / 1000;
}

// --- Scrub slider ---

scrubSlider.addEventListener('mousedown', () => { sliderDragging = true; });

scrubSlider.addEventListener('mouseup', () => {
  sliderDragging = false;
  video.currentTime = parseInt(scrubSlider.value) / 1000;
});

scrubSlider.addEventListener('input', () => {
  posLabel.textContent = msToTime(parseInt(scrubSlider.value));
  if (!sliderDragging) {
    video.currentTime = parseInt(scrubSlider.value) / 1000;
  }
});

// --- Set start / end from player position ---

setStartBtn.addEventListener('click', () => {
  startTimeIn.value = msToTime(Math.floor(video.currentTime * 1000));
  validateTimeInputs();
  updateDurationLabel();
});

setEndBtn.addEventListener('click', () => {
  endTimeIn.value = msToTime(Math.floor(video.currentTime * 1000));
  validateTimeInputs();
  updateDurationLabel();
});

// --- Detect first frame change (skip frozen intro) ---

detectStartBtn.addEventListener('click', async () => {
  const input = filePath.value;
  if (!input) return;

  detectStartBtn.disabled = true;
  const prevText = detectStartBtn.textContent;
  detectStartBtn.textContent = 'Detecting…';
  showStatus('Detecting first frame change…', '');

  try {
    const t = await go.DetectFirstChange(input);
    if (t) {
      startTimeIn.value = t;
      validateTimeInputs();
      updateDurationLabel();
      const ms = timeToMs(t);
      if (ms >= 0) {
        video.currentTime = ms / 1000;
        scrubSlider.value = ms;
        posLabel.textContent = t;
      }
      showStatus('First frame change at ' + t + ' — start set', 'success');
    } else {
      showStatus('No frozen intro detected — start left unchanged.', '');
    }
  } catch (e) {
    showStatus('Detect failed: ' + String(e), 'error');
  } finally {
    detectStartBtn.textContent = prevText;
    detectStartBtn.disabled = false;
  }
});

// --- Overwrite-source toggle ---

overwriteChk.addEventListener('change', () => {
  const on = overwriteChk.checked;
  outputPath.disabled = on;
  outputBrowseBtn.disabled = on;
});

// --- Time input validation & duration ---

function validateTimeInputs() {
  startTimeIn.classList.toggle('invalid', timeToMs(startTimeIn.value) < 0);
  endTimeIn.classList.toggle('invalid',   timeToMs(endTimeIn.value)   < 0);
}

function updateDurationLabel() {
  const startMs = timeToMs(startTimeIn.value);
  const endMs   = timeToMs(endTimeIn.value);
  if (startMs < 0 || endMs < 0) {
    durationLabel.textContent = 'Invalid time format';
    return;
  }
  const diff = endMs - startMs;
  durationLabel.textContent = diff > 0 ? `Duration: ${msToTime(diff)}` : 'Invalid range';
}

startTimeIn.addEventListener('input', () => { validateTimeInputs(); updateDurationLabel(); });
endTimeIn.addEventListener('input',   () => { validateTimeInputs(); updateDurationLabel(); });

// --- Browse output ---

el('output-browse-btn').addEventListener('click', async () => {
  const current = outputPath.value;
  let startDir = '';
  if (current) {
    const sep = current.includes('/') ? '/' : '\\';
    const idx = current.lastIndexOf(sep);
    if (idx > 0) startDir = current.substring(0, idx);
  }
  const path = await go.SaveOutputFile(startDir);
  if (path) outputPath.value = path;
});

// --- Trim ---

trimBtn.addEventListener('click', async () => {
  const input   = filePath.value;
  const output  = outputPath.value.trim();
  const replace = overwriteChk.checked;

  if (!input) {
    showStatus('Please select a valid input file.', 'error');
    return;
  }
  if (!replace && !output) {
    showStatus('Please specify an output file path.', 'error');
    return;
  }

  const startMs = timeToMs(startTimeIn.value);
  const endMs   = timeToMs(endTimeIn.value);

  if (startMs < 0 || endMs < 0) {
    showStatus('Invalid time format in start or end field.', 'error');
    return;
  }
  if (startMs >= endMs) {
    showStatus('End time must be after start time.', 'error');
    return;
  }

  if (replace) {
    const name = input.split(/[/\\]/).pop();
    const ok = await go.Confirm('Overwrite source', `Overwrite the source file "${name}" with the trimmed result? This cannot be undone.`);
    if (!ok) return;
  } else {
    const exists = await go.FileExists(output);
    if (exists) {
      const name = output.split(/[/\\]/).pop();
      const ok = await go.Confirm('File exists', `"${name}" already exists. Overwrite?`);
      if (!ok) return;
    }
  }

  video.pause();

  const params = {
    inputPath:     input,
    outputPath:    replace ? '' : output,
    startTime:     msToTime(startMs),  // normalised HH:MM:SS.mmm
    endTime:       msToTime(endMs),
    encoderMode:   encodingCombo.value,
    replaceSource: replace,
  };

  trimBtn.disabled = true;
  progressBox.classList.remove('hidden');
  showStatus('Trimming…', '');

  try {
    const result = await go.TrimVideo(params);
    showStatus(result.message, result.success ? 'success' : 'error');
    // Source was replaced — reload the preview to show the trimmed video.
    if (result.success && replace) {
      await loadVideo(input);
    }
  } catch (e) {
    showStatus('Unexpected error: ' + String(e), 'error');
  } finally {
    progressBox.classList.add('hidden');
    trimBtn.disabled = false;
  }
});

// --- Status helper ---

function showStatus(msg, type) {
  statusLabel.textContent = msg;
  statusLabel.className = 'status-label' + (type ? ' ' + type : '');
}

// --- Folder freeze scan ---

const scanFolderBtn = el('scan-folder-btn');
const scanStopBtn   = el('scan-stop-btn');
const scanWindow    = el('scan-window');
const scanStatus    = el('scan-status');
const scanLogToggle = el('scan-log-toggle');
const scanLog       = el('scan-log');
const scanTbody     = el('scan-tbody');
const scanModal     = el('scan-modal');
const scanModalTitle = el('scan-modal-title');
const scanCloseBtn  = el('scan-close-btn');

let scanning = false;

scanCloseBtn.addEventListener('click', () => scanModal.classList.add('hidden'));
scanModal.addEventListener('click', e => { if (e.target === scanModal) scanModal.classList.add('hidden'); });

scanLogToggle.addEventListener('change', () => {
  scanLog.classList.toggle('hidden', !scanLogToggle.checked);
});

function appendLog(p) {
  const n = String(p.done).padStart(String(p.total).length, ' ');
  let result;
  if (p.error) result = `⚠ ${p.error}`;
  else if (p.frozenIntro) result = `frozen → ${p.firstChange} (${(p.freezeSec || 0).toFixed(2)}s)`;
  else result = 'no freeze';
  const line = document.createElement('div');
  line.className = 'scan-log-line' + (p.error ? ' scan-err' : (p.frozenIntro ? ' scan-frozen' : ''));
  line.textContent = `[${n}/${p.total}] ${p.file} — ${result}`;
  scanLog.appendChild(line);
  scanLog.scrollTop = scanLog.scrollHeight;
}

// Live progress from the Go scanner.
if (window.runtime && window.runtime.EventsOn) {
  window.runtime.EventsOn('scan:progress', p => {
    if (!p || !p.total) return;
    scanStatus.textContent = `Scanning… ${p.done}/${p.total}`;
    appendLog(p);
  });
}

function setScanning(on) {
  scanning = on;
  scanFolderBtn.disabled = on;
  scanStopBtn.disabled = !on;
}

scanFolderBtn.addEventListener('click', async () => {
  const dir = await go.SelectFolder();
  if (!dir) return;

  const win = Math.max(1, parseFloat(scanWindow.value) || 10);
  setScanning(true);
  scanStatus.textContent = 'Scanning…';
  scanTbody.innerHTML = '';
  scanLog.innerHTML = '';

  try {
    const rows = await go.ScanFolder(dir, win);
    renderScan(rows, win);
  } catch (e) {
    scanStatus.textContent = 'Scan failed: ' + String(e);
  } finally {
    setScanning(false);
  }
});

scanStopBtn.addEventListener('click', async () => {
  scanStopBtn.disabled = true;
  scanStatus.textContent = 'Stopping…';
  await go.StopScan();
});

function renderScan(rows, win) {
  scanTbody.innerHTML = '';
  const totalFiles = rows ? rows.length : 0;
  // Drop rows for files that were never scanned (e.g. after Stop).
  rows = (rows || []).filter(r => r && r.file);
  if (rows.length === 0) {
    scanStatus.textContent = totalFiles === 0
      ? 'No video files found in that folder.'
      : 'Scan stopped before any file completed.';
    scanModal.classList.add('hidden');
    return;
  }
  const stopped = rows.length < totalFiles;

  // Frozen intros first, longest freeze first.
  rows.sort((a, b) => (b.frozenIntro - a.frozenIntro) || (b.freezeSec - a.freezeSec));

  let frozenCount = 0;
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.className = 'scan-clickable' + (r.error ? ' scan-err' : (r.frozenIntro ? ' scan-frozen' : ''));
    tr.title = 'Click to open this file in the trimmer';

    const frozenCell = r.error ? '—' : (r.frozenIntro ? 'Yes' : 'No');
    const change     = r.error ? r.error : (r.firstChange || '—');
    const secs       = r.error ? '—' : (r.frozenIntro ? r.freezeSec.toFixed(2) : '0.00');
    if (r.frozenIntro && !r.error) frozenCount++;

    tr.innerHTML =
      `<td class="scan-file">${escapeHtml(r.file)}</td>` +
      `<td>${frozenCell}</td><td>${escapeHtml(change)}</td><td>${secs}</td>`;

    // Clicking a row loads it into the trimmer and presets the detected start.
    tr.addEventListener('click', async () => {
      scanModal.classList.add('hidden');
      await loadVideo(r.path);
      if (r.frozenIntro && r.firstChange && !r.firstChange.startsWith('>')) {
        startTimeIn.value = r.firstChange;
        validateTimeInputs();
        updateDurationLabel();
      }
      showStatus('Loaded ' + r.file, 'success');
    });
    scanTbody.appendChild(tr);
  }

  const stoppedNote = stopped ? ` (stopped — ${rows.length}/${totalFiles} scanned)` : '';
  const summary = `${rows.length} file(s) — ${frozenCount} with a frozen intro (first ${win}s)${stoppedNote}`;
  scanModalTitle.textContent = 'Folder Freeze Scan — ' + summary;
  scanStatus.textContent = summary + '.';
  scanModal.classList.remove('hidden');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// --- Boot ---
initEncoders();
