// Package web embeds the small management dashboard served at /app/.
package web

import (
	"embed"
	"net/http"
)

//go:embed index.html
var files embed.FS

func Handler() http.Handler { return http.FileServer(http.FS(files)) }
