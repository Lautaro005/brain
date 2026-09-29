# Security Policy

brain runs on your own machine and handles personal data (your notes, your profile, the memory other chatbots have about you, and API keys for your connections). Security reports are taken seriously.

## Supported versions

Only the latest release gets security fixes. Update with `brain update` (or run the installer again).

| Version | Supported |
|---|---|
| Latest release (currently `v0.02.8.1`) | ✅ |
| Older releases | ❌ |

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately through GitHub:

1. Go to the repository's **Security** tab → **Report a vulnerability**
   (direct link: https://github.com/Lautaro005/brain/security/advisories/new).
2. Describe the problem, the version (`brain path` + `git -C ~/.brain log -1 --oneline`), and the steps to reproduce it. A proof of concept helps a lot.

You'll get a first answer within 7 days. Once the issue is confirmed, a fix is prepared in a private advisory and released as soon as possible; you'll be credited in the advisory and the changelog unless you prefer otherwise.

## What's in scope

brain's own code in this repository, in particular:

- **The dashboard** (`dashboard.py`, `127.0.0.1:8765`): it must only accept requests from its own page. It listens on `127.0.0.1`, rejects any `Host` other than `127.0.0.1:<port>` / `localhost:<port>` (DNS rebinding) and requires the `X-Brain: 1` header on every POST (so browsers force a CORS preflight that is never approved). A way for a website to start processes, read the vault or write to it is a vulnerability.
- **Vault path validation** (`brain_mcp/vault.py`): tools must not read or write outside `vault/` (`..`, absolute paths, hidden files, symlinks). This includes both ends of `move_file`, which also refuses to move `BRAIN.md`, `profile.md`, `memory/` and `knowledge/sources/`.
- **Secrets** (`.env`, `data/connections.json`): API keys and tokens must never be returned by the dashboard API, written to the vault, or logged.
- **Agent configs** (`brain_mcp/agents.py`): connecting an agent must never drop other entries from a client's config file or write it without a backup.
- **The installer** (`install.sh`).
- **Content rendering**: scraped pages, notes and chat answers are rendered in the dashboard, graph and chat through DOMPurify, and brain's own dialogs and dropdowns (`brain_mcp/ui.js`) escape their text; script execution from vault content or model output is a vulnerability.
- **Chat settings stored locally**: the chat system prompt and context settings stay on your machine (browser storage and `data/`); a way to read or change them from a website is a vulnerability.

## Out of scope

- Vulnerabilities in third-party software brain uses (Ollama, Chroma, Playwright/Chromium, the MCP SDK, Composio). Report those upstream.
- MCP servers you add under **Connections**: they run with your permissions by design, like any program you install.
- Attacks that require someone who already has access to your user account on the machine.
- Prompt injection that makes an agent write to your vault through the normal tools. Every write is versioned and can be undone with `file_history` + `restore_file`; reports that show a way around that history are in scope.
