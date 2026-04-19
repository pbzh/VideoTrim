package main

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

var videoMimeTypes = map[string]string{
	".mp4":  "video/mp4",
	".m4v":  "video/mp4",
	".mov":  "video/mp4",
	".webm": "video/webm",
	".mkv":  "video/x-matroska",
	".avi":  "video/x-msvideo",
	".wmv":  "video/x-ms-wmv",
	".flv":  "video/x-flv",
	".ts":   "video/mp2t",
	".mpg":  "video/mpeg",
	".mpeg": "video/mpeg",
	".3gp":  "video/3gpp",
}

// FileHandler serves local video files to the webview for preview.
// It handles GET /video?path=<absolute-path>.
type FileHandler struct{}

func NewFileHandler() *FileHandler {
	return &FileHandler{}
}

func (h *FileHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/video" {
		http.NotFound(w, r)
		return
	}

	filePath := r.URL.Query().Get("path")
	if filePath == "" {
		http.Error(w, "missing path parameter", http.StatusBadRequest)
		return
	}

	if _, err := os.Stat(filePath); err != nil {
		http.NotFound(w, r)
		return
	}

	ext := strings.ToLower(filepath.Ext(filePath))
	if mt, ok := videoMimeTypes[ext]; ok {
		w.Header().Set("Content-Type", mt)
	}
	w.Header().Set("Accept-Ranges", "bytes")

	http.ServeFile(w, r, filePath)
}
