#!/usr/bin/env bash
# Un solo comando: dashboard + Ollama + Chroma. Ctrl+C apaga lo que haya prendido.
cd "$(dirname "$0")"
uv run python dashboard.py "$@"
