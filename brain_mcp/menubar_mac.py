"""Ícono en la barra de menú de macOS + aviso en el notch cuando cambia el vault.

Proceso aparte que arranca el dashboard (`brain_mcp.menubar.start`). Requiere el grupo opcional macos:
  uv sync --group macos
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

POLL_SEC = 5


def _api(port: int, path: str, post: dict | None = None) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    headers = {"X-Brain": "1"} if post is not None else {}
    data = json.dumps(post).encode() if post is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if post else "GET")
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


def start(port: int, parent_pid: int | None = None):
    import platform
    import subprocess

    if platform.system() != "Darwin" or os.environ.get("BRAIN_NO_MENUBAR") == "1":
        return None
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys.executable, "-m", "brain_mcp.menubar_mac", str(port)]
    if parent_pid:
        cmd.append(str(parent_pid))
    try:
        return subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log.warning("no se pudo iniciar la barra de menú: %s", e)
        return None


def main() -> None:
    port = int(sys.argv[1])
    parent = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    try:
        import rumps
    except ImportError:
        sys.exit(0)

    try:
        from AppKit import NSApp, NSBackingStoreBuffered, NSColor, NSFont, NSMakeRect, NSScreen, NSStatusWindowLevel
        from AppKit import NSPanel, NSTextField, NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel
        from Foundation import NSMakeSize
        has_appkit = True
    except ImportError:
        has_appkit = False

    class NotchPanel:
        def __init__(self, app: "BrainMenuApp"):
            self.app = app
            self.panel = None
            if not has_appkit:
                return
            style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
            self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 320, 220), style, NSBackingStoreBuffered, False)
            self.panel.setLevel_(NSStatusWindowLevel + 1)
            self.panel.setOpaque_(False)
            self.panel.setBackgroundColor_(NSColor.colorWithRed_green_blue_alpha_(0.04, 0.04, 0.04, 0.96))
            self.panel.setHasShadow_(True)
            self.panel.setFloatingPanel_(True)
            self.panel.setHidesOnDeactivate_(False)
            items = [
                ("Abrir panel", "#panel"),
                ("Grafo", "#grafo"),
                ("Chat", "#chat"),
                ("Últimos cambios", "#panel"),
                ("Ajustes → Notificaciones", "#ajustes"),
            ]
            y = 180
            for label, hash_ in items:
                btn = NSTextField.alloc().initWithFrame_(NSMakeRect(16, y, 288, 28))
                btn.setStringValue_(f"› {label}")
                btn.setBezeled_(False)
                btn.setDrawsBackground_(False)
                btn.setEditable_(False)
                btn.setSelectable_(False)
                btn.setTextColor_(NSColor.whiteColor())
                btn.setFont_(NSFont.systemFontOfSize_(13))
                self.panel.contentView().addSubview_(btn)
                y -= 32
            title = NSTextField.alloc().initWithFrame_(NSMakeRect(16, 188, 288, 22))
            title.setStringValue_("Cambios en el vault")
            title.setBezeled_(False)
            title.setDrawsBackground_(False)
            title.setEditable_(False)
            title.setTextColor_(NSColor.lightGrayColor())
            title.setFont_(NSFont.boldSystemFontOfSize_(12))
            self.panel.contentView().addSubview_(title)

        def toggle(self):
            if not self.panel:
                return
            if self.panel.isVisible():
                self.panel.orderOut_(None)
                return
            screen = NSScreen.mainScreen().frame()
            w, h = 320, 220
            x = (screen.size.width - w) / 2
            y = screen.size.height - h - 4
            self.panel.setFrame_(NSMakeRect(x, y, w, h), display=True)
            self.panel.makeKeyAndOrderFront_(None)

        def open_hash(self, h: str):
            self.app.open_web(h)

    class BrainMenuApp(rumps.App):
        def __init__(self):
            super().__init__("brain", quit_button=None)
            self.port = port
            self.parent = parent
            self.activity_last = -1
            self.version = "brain"
            self.notch = NotchPanel(self)
            self._build_menu()
            threading.Thread(target=self._poll_loop, daemon=True).start()
            self._refresh_version()

        def _build_menu(self):
            self._ver_item = rumps.MenuItem(self.version, callback=None)
            self.menu = [
                self._ver_item,
                None,
                rumps.MenuItem("Open (web panel)", callback=self.on_open),
                rumps.MenuItem("Notifications", callback=self.on_notify),
                rumps.MenuItem("Settings", callback=self.on_settings),
                rumps.MenuItem("Updates", callback=self.on_updates),
                None,
                rumps.MenuItem("Close", callback=self.on_close),
            ]
            if self.notch.panel:
                self.menu.append(rumps.MenuItem("Notch…", callback=lambda _: self.notch.toggle()))

        def _refresh_version(self):
            try:
                v = _api(self.port, "/api/version")
                if v.get("ok"):
                    self.version = f"brain {v.get('current', '')}"
                    self.title = "brain"
                    self._ver_item.title = self.version
            except Exception:
                pass

        def open_web(self, fragment: str = ""):
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{self.port}/{fragment}")

        def on_open(self, _):
            self.open_web("")

        def on_notify(self, _):
            self.open_web("#ajustes")

        def on_settings(self, _):
            self.open_web("#ajustes")

        def on_updates(self, _):
            self.open_web("#ajustes")

        def on_close(self, _):
            try:
                _api(self.port, "/api/quit", {})
            except Exception:
                if self.parent:
                    os.kill(self.parent, 15)

        def _poll_loop(self):
            while True:
                try:
                    r = _api(self.port, f"/api/activity?after={self.activity_last}")
                    changes = r.get("changes") or []
                    if changes:
                        self.activity_last = max(c["id"] for c in changes)
                        rumps.notification("brain", "Vault actualizado",
                                           f"{len(changes)} cambio(s) en tus notas", sound=False)
                        self.title = "●"
                        if has_appkit:
                            self.notch.toggle()
                        def blink():
                            time.sleep(8)
                            self.title = "brain"
                        threading.Thread(target=blink, daemon=True).start()
                except Exception:
                    pass
                time.sleep(POLL_SEC)

    BrainMenuApp().run()


if __name__ == "__main__":
    main()
