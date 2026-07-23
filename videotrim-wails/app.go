package main

import (
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
}

// NewApp creates a new App instance.
func NewApp() *App {
	return &App{}
}

func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
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
	InputPath   string `json:"inputPath"`
	OutputPath  string `json:"outputPath"`
	StartTime   string `json:"startTime"` // HH:MM:SS.mmm
	EndTime     string `json:"endTime"`   // HH:MM:SS.mmm
	EncoderMode string `json:"encoderMode"` // "copy" or ffmpeg encoder name
}

// TrimResult is returned after a trim operation.
type TrimResult struct {
	Success    bool    `json:"success"`
	Message    string  `json:"message"`
	FileSizeMB float64 `json:"fileSizeMB,omitempty"`
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

// TrimVideo runs ffmpeg to trim the video with the given parameters.
func (a *App) TrimVideo(params TrimParams) TrimResult {
	if params.InputPath == "" {
		return TrimResult{Success: false, Message: "No input file specified"}
	}
	if _, err := os.Stat(params.InputPath); err != nil {
		return TrimResult{Success: false, Message: "Input file not found"}
	}
	if params.OutputPath == "" {
		return TrimResult{Success: false, Message: "No output path specified"}
	}

	absIn, _ := filepath.Abs(params.InputPath)
	absOut, _ := filepath.Abs(params.OutputPath)
	if absIn == absOut {
		return TrimResult{Success: false, Message: "Output file cannot be the same as input"}
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
		args = []string{
			"-y",
			"-ss", params.StartTime,
			"-to", params.EndTime,
			"-i", params.InputPath,
			"-c", "copy",
			"-map", "0",
			"-avoid_negative_ts", "make_zero",
			params.OutputPath,
		}
	} else {
		// Output seeking: frame-accurate re-encode
		args = []string{
			"-y",
			"-i", params.InputPath,
			"-ss", params.StartTime,
			"-to", params.EndTime,
			"-c:v", mode,
		}
		args = append(args, qualityArgsFor(mode)...)
		args = append(args,
			"-c:a", "aac",
			"-b:a", "192k",
			"-map", "0",
			"-avoid_negative_ts", "make_zero",
			params.OutputPath,
		)
	}

	cmd := exec.Command(ffmpegBin(), args...)
	out, err := cmd.CombinedOutput()

	if err != nil {
		output := string(out)
		if len(output) > 2000 {
			output = "…" + output[len(output)-2000:]
		}
		return TrimResult{
			Success: false,
			Message: fmt.Sprintf("ffmpeg error (exit %v):\n%s", err, output),
		}
	}

	stat, statErr := os.Stat(params.OutputPath)
	if statErr != nil {
		return TrimResult{Success: false, Message: "ffmpeg reported success but output file was not created"}
	}

	how := "re-encoded (" + mode + ")"
	if mode == "copy" {
		how = "lossless copy"
	}
	sizeMB := float64(stat.Size()) / (1024 * 1024)
	return TrimResult{
		Success:    true,
		Message:    fmt.Sprintf("Done — %s (%.1f MB): %s", how, sizeMB, params.OutputPath),
		FileSizeMB: sizeMB,
	}
}
