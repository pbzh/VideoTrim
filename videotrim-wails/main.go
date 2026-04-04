package main

import (
	"embed"
	"io/fs"
	"log"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
)

//go:embed all:frontend/dist
var assets embed.FS

func main() {
	app := NewApp()

	subAssets, err := fs.Sub(assets, "frontend/dist")
	if err != nil {
		log.Fatal(err)
	}

	err = wails.Run(&options.App{
		Title:     "VideoTrim",
		Width:     920,
		Height:    780,
		MinWidth:  700,
		MinHeight: 600,
		AssetServer: &assetserver.Options{
			Assets:  subAssets,
			Handler: NewFileHandler(),
		},
		BackgroundColour: &options.RGBA{R: 30, G: 30, B: 30, A: 255},
		OnStartup:        app.startup,
		Bind:             []interface{}{app},
	})

	if err != nil {
		log.Fatal(err)
	}
}
