# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Landing page (`docs/index.html`): a single static HTML file with no build step, published with GitHub Pages from `main /docs`. The app's dashboard is static HTML served by `dashboard.py` (Python stdlib), with no frontend framework.

## Users

People who use AI chat apps (Claude, ChatGPT, Gemini) and agents daily and are tired of re-explaining themselves: each app forgets who they are, and none shares what the others know. Mostly non-developers comfortable with pasting one terminal command on a Mac. Developers who already know MCP are a secondary audience.

## Product Purpose

brain is a local MCP server that gives every AI agent the user connects the same memory: their profile, memories imported from other chatbots, notes, projects, skills and saved web pages, with semantic search. It installs with one command, runs on the user's Mac, and comes with a dashboard to connect agents and manage everything. Success: a user installs it, connects Claude or ChatGPT in one click, and their agents start answering with context about them.

## Positioning

One memory shared across competing AI apps, owned and stored locally by the user, instead of each vendor's siloed memory. It imports the memory other chatbots already have, so the user doesn't start from zero.

## Capabilities and Constraints

- macOS only for now. Install: `curl -fsSL https://raw.githubusercontent.com/Lautaro005/brain/main/install.sh | bash`, then the `brain` command.
- Connects to Claude Desktop, ChatGPT desktop (Codex and ChatGPT Work modes only), Claude Code, Codex CLI, Cursor, VS Code (Copilot), Windsurf, Gemini CLI, and any client that runs local (stdio) MCP servers. It does not expose a remote URL.
- Local embeddings with Ollama (`nomic-embed-text`); vector search with Chroma; scraping with trafilatura plus a Playwright fallback.
- Version history for every write, with restore.
- Connections: brain is also an MCP client. It proxies tools from other MCP servers (local command, URL, or Composio) into every connected agent, with per-connection on/off, and auto-captures results into the vault and Chroma. Composio stores OAuth tokens in its own cloud; local-command connections keep tokens on the machine (.env).
- Dashboard tabs: Dashboard, Profile, Connect agent, Connections, Graph, Knowledge, Logs. English and Spanish UI; styled with the landing's monochrome system.
- Open source under the MIT license. Repo: https://github.com/Lautaro005/brain

## Brand Commitments

- Name: "brain", lowercase.
- Landing page: black-and-white (monochrome) theme and an English/Spanish toggle — pinned by the user.
- Product visuals on the landing are HTML/SVG recreations of the real UI (dashboard, graph, terminal), not screenshots — chosen by the user.

## Evidence on Hand

No screenshots, testimonials, user counts, benchmarks or press exist. Do not invent any. Demonstration content (sample memories, notes, names) must be illustrative and clearly not real user data.

## Product Principles

1. Your memory is yours: local, portable across AI apps, never sent anywhere.
2. One command to install, one click to connect.
3. Nothing is lost: every change can be undone.
4. Honest about limits (macOS only, ChatGPT's modes, local stdio only).
