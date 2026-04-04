package main

import (
	"net/http"
	"os"
)

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

	http.ServeFile(w, r, filePath)
}
