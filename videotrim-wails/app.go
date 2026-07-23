package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"

	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

// App is the main application struct bound to the frontend.
type App struct {
	ctx          context.Context
	currentVideo string
	mu           sync.Mutex

	scanMu     sync.Mutex
	scanCancel context.CancelFunc // set while a folder scan is running
}

// NewApp creates a new App instance.
func NewApp() *App {
	return &App{}
}

func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
}

// emit sends a runtime event to the frontend, no-op before startup sets ctx.
func (a *App) emit(event string, data interface{}) {
	if a.ctx != nil {
		wailsruntime.EventsEmit(a.ctx, event, data)
	}
}

// --- Data types ---

// VideoInfo holds metadata about a video file.
type VideoInfo struct {
	FormatName string  `json:"formatName"`
	VideoCodec string  `json:"videoCodec"`
	AudioCodec string  `json:"audioCodec"`
	DurationMs int64   `json:"durationMs"`
	FPS        float64 `json:"fps"`
	Error      string  `json:"error,omitempty"`
}

// EncoderInfo describes one hardware encoder option.
type EncoderInfo struct {
	Label   string `json:"label"`
	Encoder string `json:"encoder"`
	Hint    string `json:"hint"`
}

// TrimParams holds all parameters needed to run a trim operation.
type TrimParams struct {
	InputPath     string `json:"inputPath"`
	OutputPath    string `json:"outputPath"`
	StartTime     string `json:"startTime"` // HH:MM:SS.mmm
	EndTime       string `json:"endTime"`   // HH:MM:SS.mmm
	EncoderMode   string `json:"encoderMode"`   // "smart", "copy", or ffmpeg encoder name
	ReplaceSource bool   `json:"replaceSource"` // overwrite the input file with the result
}

// TrimResult is returned after a trim operation.
type TrimResult struct {
	Success    bool    `json:"success"`
	Message    string  `json:"message"`
	FileSizeMB float64 `json:"fileSizeMB,omitempty"`
}

// ScanRow is one file's result from a folder freeze scan.
type ScanRow struct {
	File        string  `json:"file"`        // base name
	Path        string  `json:"path"`        // absolute path
	FrozenIntro bool    `json:"frozenIntro"` // starts with a frozen/static section
	FirstChange string  `json:"firstChange"` // HH:MM:SS.mmm of first frame change ("" if none)
	FreezeSec   float64 `json:"freezeSec"`   // length of the initial freeze in seconds
	Error       string  `json:"error,omitempty"`
}

// --- Helpers ---

var extraPaths = []string{
	"/opt/homebrew/bin",
	"/usr/local/bin",
	"/usr/bin",
}

func resolveBin(name string) string {
	// Prefer a copy bundled next to our own executable (self-contained app).
	if exe, err := os.Executable(); err == nil {
		full := filepath.Join(filepath.Dir(exe), name)
		if _, err := os.Stat(full); err == nil {
			return full
		}
	}
	if path, err := exec.LookPath(name); err == nil {
		return path
	}
	for _, dir := range extraPaths {
		full := filepath.Join(dir, name)
		if _, err := os.Stat(full); err == nil {
			return full
		}
	}
	return name
}

func binName(base string) string {
	if runtime.GOOS == "windows" {
		return base + ".exe"
	}
	return base
}

func ffmpegBin() string  { return resolveBin(binName("ffmpeg")) }
func ffprobeBin() string { return resolveBin(binName("ffprobe")) }

var allHWEncoders = []EncoderInfo{
	{"H.264 (Apple VideoToolbox)", "h264_videotoolbox", "Hardware-accelerated H.264 — frame-accurate, fast"},
	{"HEVC (Apple VideoToolbox)", "hevc_videotoolbox", "Hardware-accelerated HEVC — frame-accurate, smaller files"},
	{"H.264 (AMD AMF)", "h264_amf", "Hardware-accelerated H.264 via AMD (Windows)"},
	{"HEVC (AMD AMF)", "hevc_amf", "Hardware-accelerated HEVC via AMD (Windows) — smaller files"},
	{"AV1 (AMD AMF)", "av1_amf", "Hardware-accelerated AV1 via AMD RDNA3+ (Windows) — best compression"},
	{"H.264 (Intel QSV)", "h264_qsv", "Hardware-accelerated H.264 via Intel Quick Sync"},
	{"HEVC (Intel QSV)", "hevc_qsv", "Hardware-accelerated HEVC via Intel Quick Sync"},
	{"AV1 (Intel QSV)", "av1_qsv", "Hardware-accelerated AV1 via Intel Quick Sync — best compression"},
}

// qualityArgsFor returns near-lossless CQP-style quality args for a given
// hardware encoder, minimizing quality degradation on re-encode.
func qualityArgsFor(encoder string) []string {
	switch {
	case strings.Contains(encoder, "videotoolbox"):
		// VideoToolbox -q:v is 1-100, higher = better. 80 ≈ visually lossless.
		return []string{"-q:v", "80"}
	case strings.Contains(encoder, "amf"):
		// AMD AMF: constant-QP, lower = better. qp 16 ≈ near-lossless.
		// No -qp_b: av1_amf has no B-frames and would reject it.
		return []string{"-rc", "cqp", "-qp_i", "16", "-qp_p", "16", "-quality", "quality"}
	case strings.Contains(encoder, "qsv"):
		// Intel QSV: ICQ global_quality, lower = better. 16 ≈ near-lossless.
		return []string{"-global_quality", "16"}
	case strings.Contains(encoder, "libx26"):
		// Software x264/x265: CRF, lower = better. 16 ≈ near-lossless.
		return []string{"-crf", "16", "-preset", "medium"}
	}
	return nil
}

// timeToSeconds parses an HH:MM:SS.mmm (or SS.mmm) string into seconds.
func timeToSeconds(t string) (float64, bool) {
	parts := strings.Split(strings.TrimSpace(t), ":")
	var secs float64
	for _, p := range parts {
		v, err := strconv.ParseFloat(p, 64)
		if err != nil {
			return 0, false
		}
		secs = secs*60 + v
	}
	return secs, true
}

// startOnKeyframe reports whether startTime lands (within one-frame tolerance)
// on a video keyframe, meaning a lossless stream-copy cut is possible there.
func (a *App) startOnKeyframe(input, startTime string) bool {
	start, ok := timeToSeconds(startTime)
	if !ok {
		return false
	}
	// Probe keyframes in a short window at/after the start time.
	interval := fmt.Sprintf("%.3f%%+0.5", start)
	out, err := exec.Command(ffprobeBin(),
		"-v", "error",
		"-select_streams", "v:0",
		"-skip_frame", "nokey",
		"-show_entries", "frame=best_effort_timestamp_time",
		"-read_intervals", interval,
		"-of", "csv=p=0",
		input,
	).Output()
	if err != nil {
		return false
	}
	const tol = 0.010 // ~one frame at high fps
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		ts, err := strconv.ParseFloat(strings.TrimSpace(line), 64)
		if err != nil {
			continue
		}
		if ts-start >= -tol && ts-start <= tol {
			return true
		}
	}
	return false
}

// smartFallbackEncoder picks the best available hardware encoder for a
// frame-accurate re-encode, preferring the current platform's HW, and falling
// back to software x264 if no hardware encoder is detected.
func smartFallbackEncoder() string {
	var prefer []string
	if runtime.GOOS == "darwin" {
		prefer = []string{"h264_videotoolbox"}
	} else {
		prefer = []string{"h264_amf", "h264_qsv"}
	}
	out, err := exec.Command(ffmpegBin(), "-encoders", "-hide_banner").Output()
	if err == nil {
		output := string(out)
		for _, enc := range prefer {
			if strings.Contains(output, enc) {
				return enc
			}
		}
	}
	return "libx264"
}

// videoExts is the set of file extensions treated as scannable videos.
var videoExts = map[string]bool{
	".mp4": true, ".mkv": true, ".avi": true, ".mov": true, ".ts": true,
	".flv": true, ".wmv": true, ".webm": true, ".m4v": true, ".mpg": true,
	".mpeg": true, ".3gp": true,
}

// parseInitialFreeze reports whether the video starts frozen (a freeze_start
// at/near t=0), and if so the timestamp where that freeze ends.
func parseInitialFreeze(text string) (frozen bool, end float64, hasEnd bool) {
	for _, line := range strings.Split(text, "\n") {
		switch {
		case strings.Contains(line, "freeze_start:"):
			if v, ok := lastFloatAfter(line, "freeze_start:"); ok && v < 0.5 {
				frozen = true
			}
		case strings.Contains(line, "freeze_end:") && frozen && !hasEnd:
			if v, ok := lastFloatAfter(line, "freeze_end:"); ok {
				end, hasEnd = v, true
			}
		}
	}
	return
}

// parseInitialFreezeEnd scans ffmpeg freezedetect output and returns the
// timestamp (seconds) where an initial freeze ends — the moment of the first
// real frame change. Returns -1 when the video does not start frozen.
//
// freezedetect emits lines like:
//
//	[freezedetect @ 0x...] freeze_start: 0.000000
//	[freezedetect @ 0x...] freeze_end: 2.167
func parseInitialFreezeEnd(text string) float64 {
	foundZeroStart := false
	for _, line := range strings.Split(text, "\n") {
		switch {
		case strings.Contains(line, "freeze_start:"):
			if v, ok := lastFloatAfter(line, "freeze_start:"); ok && v < 0.5 {
				foundZeroStart = true
			}
		case strings.Contains(line, "freeze_end:") && foundZeroStart:
			if v, ok := lastFloatAfter(line, "freeze_end:"); ok {
				return v
			}
		}
	}
	return -1
}

// lastFloatAfter parses the float immediately following the given marker.
func lastFloatAfter(line, marker string) (float64, bool) {
	idx := strings.Index(line, marker)
	if idx < 0 {
		return 0, false
	}
	rest := strings.TrimSpace(line[idx+len(marker):])
	end := 0
	for end < len(rest) && (rest[end] == '.' || rest[end] == '-' || (rest[end] >= '0' && rest[end] <= '9')) {
		end++
	}
	if end == 0 {
		return 0, false
	}
	v, err := strconv.ParseFloat(rest[:end], 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

// DetectFirstChange finds the timestamp of the first real frame change (end of
// an initial frozen/static intro) and returns it as HH:MM:SS.mmm. Returns an
// empty string when the video does not start with a freeze.
//
// ffmpeg's freezedetect logs the freeze boundaries to stderr as it decodes.
// We stream that output and kill ffmpeg the moment the first freeze_end is
// found, so detection only decodes up to the first frame change instead of the
// whole file — the difference between ~2s and minutes on a long video.
func (a *App) DetectFirstChange(path string) string {
	if path == "" {
		return ""
	}
	cmd := exec.Command(ffmpegBin(),
		"-hide_banner",
		"-i", path,
		"-vf", "freezedetect=n=-40dB:d=1",
		"-map", "0:v:0",
		"-f", "null", "-",
	)
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return ""
	}
	if err := cmd.Start(); err != nil {
		return ""
	}

	var buf strings.Builder
	var result string
	scanner := bufio.NewScanner(stderr)
	for scanner.Scan() {
		buf.WriteString(scanner.Text())
		buf.WriteByte('\n')
		if secs := parseInitialFreezeEnd(buf.String()); secs > 0 {
			result = secondsToTime(secs)
			_ = cmd.Process.Kill() // stop decoding immediately
			break
		}
	}
	_ = cmd.Wait() // reap the process (killed or finished)
	return result
}

// SelectFolder shows a directory-picker dialog and returns the chosen path.
func (a *App) SelectFolder() string {
	dir, err := wailsruntime.OpenDirectoryDialog(a.ctx, wailsruntime.OpenDialogOptions{
		Title: "Select Folder to Scan",
	})
	if err != nil {
		return ""
	}
	return dir
}

// scanFreeze runs freezedetect over the first windowSec seconds of a file and
// returns the row describing its initial-freeze state.
func scanFreeze(ctx context.Context, path string, windowSec float64) ScanRow {
	row := ScanRow{File: filepath.Base(path), Path: path}
	// -t limits decoding to the window, so each file costs ~windowSec of decode.
	cmd := exec.CommandContext(ctx, ffmpegBin(),
		"-hide_banner",
		"-i", path,
		"-t", strconv.FormatFloat(windowSec, 'f', 3, 64),
		"-vf", "freezedetect=n=-40dB:d=1",
		"-map", "0:v:0",
		"-an",
		"-f", "null", "-",
	)
	out, err := cmd.CombinedOutput()
	text := string(out)
	if ctx.Err() != nil {
		row.Error = "stopped"
		return row
	}
	if err != nil && !strings.Contains(text, "freeze") {
		// A real failure (bad file, no video stream) — surface a short reason.
		row.Error = "probe failed"
		return row
	}
	frozen, end, hasEnd := parseInitialFreeze(text)
	if !frozen {
		return row // no frozen intro
	}
	row.FrozenIntro = true
	if hasEnd {
		row.FreezeSec = end
		row.FirstChange = secondsToTime(end)
	} else {
		// Frozen for the entire scanned window — first change is beyond it.
		row.FreezeSec = windowSec
		row.FirstChange = ">" + secondsToTime(windowSec)
	}
	return row
}

// ScanFolder scans every video file in dir for a frozen/static intro within the
// first windowSec seconds and returns one row per file. Files are scanned
// concurrently; progress is emitted via the "scan:progress" event.
func (a *App) ScanFolder(dir string, windowSec float64) []ScanRow {
	if dir == "" {
		return nil
	}
	if windowSec <= 0 {
		windowSec = 10
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var paths []string
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		if videoExts[strings.ToLower(filepath.Ext(e.Name()))] {
			paths = append(paths, filepath.Join(dir, e.Name()))
		}
	}

	rows := make([]ScanRow, len(paths))
	total := len(paths)
	if total == 0 {
		return rows
	}

	// Cancellable context so StopScan can abort in-flight ffmpeg processes.
	ctx, cancel := context.WithCancel(context.Background())
	a.scanMu.Lock()
	if a.scanCancel != nil {
		a.scanCancel() // cancel any previous scan still running
	}
	a.scanCancel = cancel
	a.scanMu.Unlock()
	defer func() {
		a.scanMu.Lock()
		a.scanCancel = nil
		a.scanMu.Unlock()
		cancel()
	}()

	// Bounded worker pool — freezedetect is CPU-bound on decode.
	workers := runtime.NumCPU()
	if workers > 6 {
		workers = 6
	}
	if workers < 1 {
		workers = 1
	}

	var wg sync.WaitGroup
	var doneMu sync.Mutex
	done := 0
	sem := make(chan struct{}, workers)

	for i, p := range paths {
		if ctx.Err() != nil {
			break // stopped — don't dispatch remaining files
		}
		wg.Add(1)
		sem <- struct{}{}
		go func(idx int, path string) {
			defer wg.Done()
			defer func() { <-sem }()
			row := scanFreeze(ctx, path, windowSec)
			rows[idx] = row
			doneMu.Lock()
			done++
			d := done
			doneMu.Unlock()
			a.emit("scan:progress", map[string]interface{}{
				"done":        d,
				"total":       total,
				"file":        row.File,
				"frozenIntro": row.FrozenIntro,
				"firstChange": row.FirstChange,
				"freezeSec":   row.FreezeSec,
				"error":       row.Error,
			})
		}(i, p)
	}
	wg.Wait()
	return rows
}

// StopScan cancels an in-progress folder scan, if any.
func (a *App) StopScan() {
	a.scanMu.Lock()
	defer a.scanMu.Unlock()
	if a.scanCancel != nil {
		a.scanCancel()
	}
}

// secondsToTime formats seconds as HH:MM:SS.mmm.
func secondsToTime(secs float64) string {
	if secs < 0 {
		secs = 0
	}
	totalMs := int64(secs*1000 + 0.5)
	ms := totalMs % 1000
	totalSec := totalMs / 1000
	s := totalSec % 60
	m := (totalSec / 60) % 60
	h := totalSec / 3600
	return fmt.Sprintf("%02d:%02d:%02d.%03d", h, m, s, ms)
}

// --- Bound methods ---

// GetAvailableEncoders probes ffmpeg for available hardware encoders.
func (a *App) GetAvailableEncoders() []EncoderInfo {
	out, err := exec.Command(ffmpegBin(), "-encoders", "-hide_banner").Output()
	if err != nil {
		return nil
	}
	output := string(out)
	var available []EncoderInfo
	for _, enc := range allHWEncoders {
		if strings.Contains(output, enc.Encoder) {
			available = append(available, enc)
		}
	}
	return available
}

// GetVideoInfo returns video metadata for the given file path.
func (a *App) GetVideoInfo(path string) VideoInfo {
	out, err := exec.Command(ffprobeBin(),
		"-v", "quiet",
		"-print_format", "json",
		"-show_streams",
		"-show_format",
		path,
	).Output()
	if err != nil {
		return VideoInfo{Error: "ffprobe failed: " + err.Error()}
	}

	var data map[string]interface{}
	if err := json.Unmarshal(out, &data); err != nil {
		return VideoInfo{Error: "JSON parse error"}
	}

	format, ok := data["format"].(map[string]interface{})
	if !ok {
		return VideoInfo{Error: "No video format found — is ffprobe installed?"}
	}

	info := VideoInfo{}
	info.FormatName, _ = format["format_name"].(string)

	if durStr, ok := format["duration"].(string); ok {
		if dur, err := strconv.ParseFloat(durStr, 64); err == nil {
			info.DurationMs = int64(dur * 1000)
		}
	}

	streams, _ := data["streams"].([]interface{})
	for _, s := range streams {
		stream, ok := s.(map[string]interface{})
		if !ok {
			continue
		}
		codecType, _ := stream["codec_type"].(string)
		codecName, _ := stream["codec_name"].(string)
		switch codecType {
		case "video":
			if info.VideoCodec == "" {
				info.VideoCodec = codecName
				if rFPS, _ := stream["r_frame_rate"].(string); strings.Contains(rFPS, "/") {
					parts := strings.SplitN(rFPS, "/", 2)
					num, _ := strconv.ParseFloat(parts[0], 64)
					den, _ := strconv.ParseFloat(parts[1], 64)
					if den > 0 && num > 0 {
						info.FPS = num / den
					}
				}
			}
		case "audio":
			if info.AudioCodec == "" {
				info.AudioCodec = codecName
			}
		}
	}

	if info.VideoCodec == "" {
		return VideoInfo{Error: "No video stream found in file"}
	}
	return info
}

// OpenVideoFile shows a file-open dialog and returns the selected path.
func (a *App) OpenVideoFile() string {
	path, err := wailsruntime.OpenFileDialog(a.ctx, wailsruntime.OpenDialogOptions{
		Title: "Select Video",
		Filters: []wailsruntime.FileFilter{
			{
				DisplayName: "Video Files (*.mp4 *.mkv *.avi *.mov *.ts *.flv *.wmv *.webm *.m4v *.mpg *.mpeg *.3gp)",
				Pattern:     "*.mp4;*.mkv;*.avi;*.mov;*.ts;*.flv;*.wmv;*.webm;*.m4v;*.mpg;*.mpeg;*.3gp",
			},
			{DisplayName: "All Files", Pattern: "*"},
		},
	})
	if err != nil || path == "" {
		return ""
	}
	a.mu.Lock()
	a.currentVideo = path
	a.mu.Unlock()
	return path
}

// SaveOutputFile shows a save dialog and returns the chosen path.
func (a *App) SaveOutputFile(startDir string) string {
	path, err := wailsruntime.SaveFileDialog(a.ctx, wailsruntime.SaveDialogOptions{
		Title:            "Save As",
		DefaultDirectory: startDir,
		Filters: []wailsruntime.FileFilter{
			{DisplayName: "Video Files", Pattern: "*.mp4;*.mkv;*.avi;*.mov;*.ts;*.flv;*.wmv;*.webm;*.m4v;*.mpg;*.mpeg;*.3gp"},
			{DisplayName: "All Files", Pattern: "*"},
		},
	})
	if err != nil {
		return ""
	}
	return path
}

// AutoOutputPath generates an output path by appending _trimmed to the input filename.
func (a *App) AutoOutputPath(inputPath string) string {
	ext := filepath.Ext(inputPath)
	base := strings.TrimSuffix(inputPath, ext)
	return base + "_trimmed" + ext
}

// FileExists reports whether a file exists at the given path.
func (a *App) FileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// Confirm shows a native yes/no dialog and returns true if the user confirms.
// Used instead of the webview's window.confirm(), which WKWebView on macOS does
// not reliably handle (it silently resolves to false).
func (a *App) Confirm(title, message string) bool {
	sel, err := wailsruntime.MessageDialog(a.ctx, wailsruntime.MessageDialogOptions{
		Type:          wailsruntime.QuestionDialog,
		Title:         title,
		Message:       message,
		Buttons:       []string{"Yes", "No"},
		DefaultButton: "Yes",
		CancelButton:  "No",
	})
	if err != nil {
		return false
	}
	// On macOS the returned value is the button label; treat anything but an
	// explicit "No"/"Cancel" as confirmation to be robust across platforms.
	return sel != "No" && sel != "Cancel" && sel != ""
}

// TrimVideo runs ffmpeg to trim the video with the given parameters.
func (a *App) TrimVideo(params TrimParams) TrimResult {
	if params.InputPath == "" {
		return TrimResult{Success: false, Message: "No input file specified"}
	}
	if _, err := os.Stat(params.InputPath); err != nil {
		return TrimResult{Success: false, Message: "Input file not found"}
	}

	// outPath is where ffmpeg actually writes. When replacing the source we
	// write to a sibling temp file first, then atomically rename it over the
	// input once ffmpeg succeeds.
	outPath := params.OutputPath
	if params.ReplaceSource {
		ext := filepath.Ext(params.InputPath)
		base := strings.TrimSuffix(params.InputPath, ext)
		outPath = base + ".vt_tmp" + ext
	} else {
		if params.OutputPath == "" {
			return TrimResult{Success: false, Message: "No output path specified"}
		}
		absIn, _ := filepath.Abs(params.InputPath)
		absOut, _ := filepath.Abs(params.OutputPath)
		if absIn == absOut {
			return TrimResult{Success: false, Message: "Output file cannot be the same as input — enable \"Overwrite source\" to replace it"}
		}
	}

	var args []string
	mode := params.EncoderMode
	// Smart mode: stream-copy (lossless) when the start lands on a keyframe,
	// otherwise fall through to a frame-accurate near-lossless re-encode.
	if mode == "smart" {
		if a.startOnKeyframe(params.InputPath, params.StartTime) {
			mode = "copy"
		} else {
			mode = smartFallbackEncoder()
		}
	}

	if mode == "copy" {
		// Input seeking: fast, lossless, cuts on nearest keyframe
		args = []string{"-y", "-ss", params.StartTime}
		if params.EndTime != "" {
			args = append(args, "-to", params.EndTime)
		}
		args = append(args,
			"-i", params.InputPath,
			"-c", "copy",
			"-map", "0",
			"-avoid_negative_ts", "make_zero",
			outPath,
		)
	} else {
		// Output seeking: frame-accurate re-encode
		args = []string{"-y", "-i", params.InputPath, "-ss", params.StartTime}
		if params.EndTime != "" {
			args = append(args, "-to", params.EndTime)
		}
		args = append(args, "-c:v", mode)
		args = append(args, qualityArgsFor(mode)...)
		args = append(args,
			"-c:a", "aac",
			"-b:a", "192k",
			"-map", "0",
			"-avoid_negative_ts", "make_zero",
			outPath,
		)
	}

	cmd := exec.Command(ffmpegBin(), args...)
	out, err := cmd.CombinedOutput()

	if err != nil {
		if params.ReplaceSource {
			os.Remove(outPath) // clean up partial temp file
		}
		output := string(out)
		if len(output) > 2000 {
			output = "…" + output[len(output)-2000:]
		}
		return TrimResult{
			Success: false,
			Message: fmt.Sprintf("ffmpeg error (exit %v):\n%s", err, output),
		}
	}

	stat, statErr := os.Stat(outPath)
	if statErr != nil {
		return TrimResult{Success: false, Message: "ffmpeg reported success but output file was not created"}
	}
	sizeMB := float64(stat.Size()) / (1024 * 1024)

	finalPath := outPath
	if params.ReplaceSource {
		// Atomically replace the source with the trimmed temp file.
		if err := os.Rename(outPath, params.InputPath); err != nil {
			os.Remove(outPath)
			return TrimResult{Success: false, Message: "Trim succeeded but could not replace source file: " + err.Error()}
		}
		finalPath = params.InputPath
	}

	how := "re-encoded (" + mode + ")"
	if mode == "copy" {
		how = "lossless copy"
	}
	verb := "Saved"
	if params.ReplaceSource {
		verb = "Replaced source"
	}
	return TrimResult{
		Success:    true,
		Message:    fmt.Sprintf("Done — %s, %s (%.1f MB): %s", how, verb, sizeMB, finalPath),
		FileSizeMB: sizeMB,
	}
}

// BatchItem is one file to trim in a batch: from StartTime to the end of file.
type BatchItem struct {
	Path      string `json:"path"`
	StartTime string `json:"startTime"` // HH:MM:SS.mmm
}

// BatchTrimResult summarizes a batch trim run.
type BatchTrimResult struct {
	Total     int      `json:"total"`
	Succeeded int      `json:"succeeded"`
	Failed    int      `json:"failed"`
	Errors    []string `json:"errors,omitempty"`
}

// BatchTrim trims each item from its start time to the end of the file,
// replacing the source in place. Progress is emitted via "batch:progress".
// The scan's Stop button (StopScan) also cancels an in-flight batch.
func (a *App) BatchTrim(items []BatchItem) BatchTrimResult {
	res := BatchTrimResult{Total: len(items)}
	if len(items) == 0 {
		return res
	}

	ctx, cancel := context.WithCancel(context.Background())
	a.scanMu.Lock()
	if a.scanCancel != nil {
		a.scanCancel()
	}
	a.scanCancel = cancel
	a.scanMu.Unlock()
	defer func() {
		a.scanMu.Lock()
		a.scanCancel = nil
		a.scanMu.Unlock()
		cancel()
	}()

	for i, it := range items {
		if ctx.Err() != nil {
			break // stopped
		}
		r := a.TrimVideo(TrimParams{
			InputPath:     it.Path,
			StartTime:     it.StartTime,
			EndTime:       "", // to end of file
			EncoderMode:   "smart",
			ReplaceSource: true,
		})
		if r.Success {
			res.Succeeded++
		} else {
			res.Failed++
			res.Errors = append(res.Errors, filepath.Base(it.Path)+": "+r.Message)
		}
		a.emit("batch:progress", map[string]interface{}{
			"done":    i + 1,
			"total":   len(items),
			"file":    filepath.Base(it.Path),
			"success": r.Success,
			"message": r.Message,
		})
	}
	return res
}
