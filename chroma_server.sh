#!/usr/bin/env bash
# Server de Chroma compartido por todos los clientes MCP. Levantarlo antes que Claude.
cd "$(dirname "$0")"
uv run chroma run --path ./data/chroma --host 127.0.0.1 --port "${BRAIN_CHROMA_PORT:-8055}"
