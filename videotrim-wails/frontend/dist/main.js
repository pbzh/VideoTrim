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
const durationLabel = el('duration-label');
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
  trimBtn.disabled       = !on;
  scrubSlider.disabled   = !on;
}

// --- Initialise encoder list ---

async function initEncoders() {
  encoders = await go.GetAvailableEncoders() || [];

  encodingCombo.innerHTML = '';
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

  // Point <video> at the Go file-server endpoint
  video.src = `/video?path=${encodeURIComponent(path)}`;
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
  const input  = filePath.value;
  const output = outputPath.value.trim();

  if (!input) {
    showStatus('Please select a valid input file.', 'error');
    return;
  }
  if (!output) {
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

  const exists = await go.FileExists(output);
  if (exists) {
    const name = output.split(/[/\\]/).pop();
    if (!confirm(`"${name}" already exists. Overwrite?`)) return;
  }

  video.pause();

  const params = {
    inputPath:   input,
    outputPath:  output,
    startTime:   msToTime(startMs),  // normalised HH:MM:SS.mmm
    endTime:     msToTime(endMs),
    encoderMode: encodingCombo.value,
  };

  trimBtn.disabled = true;
  progressBox.classList.remove('hidden');
  showStatus('Trimming…', '');

  try {
    const result = await go.TrimVideo(params);
    showStatus(result.message, result.success ? 'success' : 'error');
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

// --- Boot ---
initEncoders();
