//! Notch panel: a black island that grows out of the top center of the screen. On a Mac with a notch it
//! comes out of the notch itself (same black, so there is no seam); elsewhere it drops from the top edge.
//! Two sizes:
//! - `alert`: a small strip with the last changes (Settings → notch instead of a system notification)
//! - `graph`: the vault graph, opened by a left click on the tray icon or by tapping the alert
//!
//! The growing is done by the page (`ui/notch.js`, CSS transitions on the GPU), not by resizing the window
//! frame by frame: this side only puts a transparent window at the top center, big enough for the island
//! to grow inside it, shrinks it to fit once the island has settled, and hides it after the island closed.
//! The page has no network access; the graph arrives through `notch_graph`.

use crate::{applog, AppState};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, WebviewUrl, WebviewWindowBuilder,
};

/// Width of the physical notch (14"/16" MacBooks are 185–200 pt). The island is wider, so it covers it.
const NOTCH_W: f64 = 190.0;
/// Height of the header row: a menu bar high (the notch is 32–38 pt), where the bell and the name go.
const HEADER_H: f64 = 34.0;
/// Room around the island inside the window, for the spring overshoot of the animation.
const PAD: f64 = 20.0;
const ALERT: (f64, f64) = (352.0, 64.0);
const GRAPH: (f64, f64) = (500.0, 300.0);
/// How long the island needs to finish growing or closing (the CSS transition is .6 s).
const SETTLE: Duration = Duration::from_millis(720);

struct Live {
    mode: String,
    shown: bool,
    /// Window size (logical) as last set, island plus padding.
    w: f64,
    h: f64,
    /// True only after the island itself has been focused, so the unfocused show doesn't immediately close it.
    focused: bool,
}

fn live() -> &'static Mutex<Live> {
    static LIVE: Mutex<Live> = Mutex::new(Live {
        mode: String::new(),
        shown: false,
        w: 0.0,
        h: 0.0,
        focused: false,
    });
    &LIVE
}

fn generation() -> &'static AtomicU64 {
    static GEN: AtomicU64 = AtomicU64::new(0);
    &GEN
}

struct Layout {
    origin_x: f64,
    top: f64,
    screen_w: f64,
    /// A physical notch is here: the island grows out of it.
    notch: bool,
    header: f64,
}

/// Island size (without the padding) for a mode.
fn island(mode: &str, header: f64) -> (f64, f64) {
    if mode == "alert" {
        (ALERT.0, header + ALERT.1)
    } else {
        (GRAPH.0, header + GRAPH.1)
    }
}

/// What the page needs to size and style the island.
fn info(lay: &Layout) -> Value {
    let (aw, ah) = island("alert", lay.header);
    let (gw, gh) = island("graph", lay.header);
    json!({
        "os": std::env::consts::OS,
        "notch": lay.notch,
        "notch_w": NOTCH_W,
        "header": lay.header,
        "alert": [aw, ah],
        "graph": [gw, gh],
    })
}

/// Drops tag, entity, folder and index nodes so the island stays readable, and caps the rest.
pub fn slim_graph(graph: &Value) -> Value {
    let keep = |k: &str| {
        matches!(
            k,
            "project" | "subproject" | "file" | "memory" | "skill" | "profile" | "source" | "note"
        )
    };
    let nodes = graph["nodes"].as_array().cloned().unwrap_or_default();
    let links = graph["links"].as_array().cloned().unwrap_or_default();
    let mut degree: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    for l in &links {
        for end in ["source", "target"] {
            if let Some(id) = l[end].as_str() {
                *degree.entry(id.to_string()).or_default() += 1;
            }
        }
    }
    let mut picked: Vec<&Value> = nodes
        .iter()
        .filter(|n| keep(n["kind"].as_str().unwrap_or("")))
        .collect();
    picked.sort_by(|a, b| {
        let rank = |k: &str| match k {
            "project" | "profile" => 0,
            "subproject" | "skill" | "memory" => 1,
            _ => 2,
        };
        rank(a["kind"].as_str().unwrap_or(""))
            .cmp(&rank(b["kind"].as_str().unwrap_or("")))
            .then(
                degree
                    .get(b["id"].as_str().unwrap_or(""))
                    .unwrap_or(&0)
                    .cmp(degree.get(a["id"].as_str().unwrap_or("")).unwrap_or(&0)),
            )
    });
    picked.truncate(42);
    let ids: std::collections::HashSet<&str> =
        picked.iter().filter_map(|n| n["id"].as_str()).collect();
    let link_ok = |k: &str| matches!(k, "part" | "related" | "link" | "memory");
    let out_links: Vec<Value> = links
        .iter()
        .filter(|l| {
            link_ok(l["kind"].as_str().unwrap_or(""))
                && ids.contains(l["source"].as_str().unwrap_or(""))
                && ids.contains(l["target"].as_str().unwrap_or(""))
        })
        .cloned()
        .collect();
    let out_nodes: Vec<Value> = picked
        .iter()
        .map(|n| {
            let mut label = n["label"].as_str().unwrap_or("").to_string();
            if label.chars().count() > 28 {
                label = label.chars().take(26).collect::<String>() + "…";
            }
            json!({
                "id": n["id"],
                "label": label,
                "kind": n["kind"],
                "path": n["path"],
            })
        })
        .collect();
    json!({ "nodes": out_nodes, "links": out_links })
}

fn window(app: &AppHandle) -> Result<tauri::WebviewWindow, String> {
    if let Some(w) = app.get_webview_window("notch") {
        return Ok(w);
    }
    let w = WebviewWindowBuilder::new(app, "notch", WebviewUrl::App("notch.html".into()))
        .title("brain")
        .inner_size(GRAPH.0 + 2.0 * PAD, GRAPH.1 + HEADER_H + PAD)
        .decorations(false)
        .transparent(true)
        // resizable on purpose: GTK ignores set_size on a non-resizable window, and the island must
        // shrink to its content (the margin around it is transparent and keeps the edge grips away)
        .resizable(true)
        .maximizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .focused(false)
        .visible(false)
        .shadow(false)
        .visible_on_all_workspaces(true)
        .build()
        .map_err(|e| e.to_string())?;
    let app_for_blur = app.clone();
    w.on_window_event(move |event| {
        if let tauri::WindowEvent::Focused(focused) = event {
            let was = live()
                .lock()
                .map(|mut l| {
                    let prev = l.focused;
                    l.focused = *focused;
                    prev && !focused && l.shown && l.mode == "graph"
                })
                .unwrap_or(false);
            if was {
                let app = app_for_blur.clone();
                thread::spawn(move || dismiss(&app));
            }
        }
    });
    lift_over_menu_bar(app, &w);
    Ok(w)
}

/// Creates the (hidden) window at startup, so its page is loaded by the time the first alert arrives.
pub fn prepare(app: &AppHandle) {
    if let Err(e) = window(app) {
        applog::line(format!("notch: could not create the window: {e}"));
    }
}

/// On macOS the island has to paint *over* the menu bar, in the notch's own black. Below the bar it
/// reads as a square stuck underneath.
#[cfg(target_os = "macos")]
fn lift_over_menu_bar(app: &AppHandle, window: &tauri::WebviewWindow) {
    let app = app.clone();
    let window = window.clone();
    let _ = app.run_on_main_thread(move || unsafe {
        use objc2::runtime::AnyObject;
        use objc2::{class, msg_send};
        let ptr = match window.ns_window() {
            Ok(p) if !p.is_null() => p,
            _ => return,
        };
        let ns_window = ptr as *mut AnyObject;
        // NSStatusWindowLevel (25) sits just above the menu bar.
        let _: () = msg_send![ns_window, setLevel: 25isize];
        let current: usize = msg_send![ns_window, collectionBehavior];
        // CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary
        let behavior = current | 1usize | (1 << 4) | (1 << 6) | (1 << 8);
        let _: () = msg_send![ns_window, setCollectionBehavior: behavior];
        let _: () = msg_send![ns_window, setOpaque: false];
        let _: () = msg_send![ns_window, setHasShadow: false];
        let _: () = msg_send![ns_window, setHidesOnDeactivate: false];
        let color: *mut AnyObject = msg_send![class!(NSColor), clearColor];
        let _: () = msg_send![ns_window, setBackgroundColor: color];
    });
}

#[cfg(not(target_os = "macos"))]
fn lift_over_menu_bar(_app: &AppHandle, _window: &tauri::WebviewWindow) {}

/// Height of the notch of the menu-bar screen in points, 0 when it has none (or before macOS 12).
#[cfg(target_os = "macos")]
fn notch_height() -> f64 {
    use objc2::encode::{Encode, Encoding};
    use objc2::runtime::{AnyObject, Bool};
    use objc2::{class, msg_send, sel};

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct Insets {
        top: f64,
        left: f64,
        bottom: f64,
        right: f64,
    }
    // SAFETY: NSEdgeInsets is four CGFloats (f64 on every supported Mac), in this order.
    unsafe impl Encode for Insets {
        const ENCODING: Encoding = Encoding::Struct(
            "NSEdgeInsets",
            &[
                Encoding::Double,
                Encoding::Double,
                Encoding::Double,
                Encoding::Double,
            ],
        );
    }

    unsafe {
        let screens: *mut AnyObject = msg_send![class!(NSScreen), screens];
        if screens.is_null() {
            return 0.0;
        }
        // the first screen is the one with the menu bar
        let first: *mut AnyObject = msg_send![screens, firstObject];
        if first.is_null() {
            return 0.0;
        }
        let ok: Bool = msg_send![first, respondsToSelector: sel!(safeAreaInsets)];
        if !ok.as_bool() {
            return 0.0;
        }
        let insets: Insets = msg_send![first, safeAreaInsets];
        insets.top.max(0.0)
    }
}

#[cfg(not(target_os = "macos"))]
fn notch_height() -> f64 {
    0.0
}

/// The screen to grow on: where the pointer is (a click on the tray icon), otherwise the main one.
fn layout(app: &AppHandle, at_cursor: bool) -> Option<Layout> {
    let by_cursor = if at_cursor {
        app.cursor_position()
            .ok()
            .and_then(|p| app.monitor_from_point(p.x, p.y).ok().flatten())
    } else {
        None
    };
    let m = by_cursor
        .or_else(|| app.primary_monitor().ok().flatten())
        .or_else(|| {
            app.get_webview_window("notch")
                .and_then(|w| w.current_monitor().ok().flatten())
        })?;
    let scale = m.scale_factor();
    let origin_x = m.position().x as f64 / scale;
    let screen_w = m.size().width as f64 / scale;
    // macOS: the physical top, so the island paints over the menu bar. Elsewhere: below whatever
    // panel the system already put at the top.
    let top = {
        #[cfg(target_os = "macos")]
        {
            m.position().y as f64 / scale
        }
        #[cfg(not(target_os = "macos"))]
        {
            m.work_area().position.y as f64 / scale
        }
    };
    let on_main_screen = m.position().x == 0 && m.position().y == 0;
    let nh = if on_main_screen { notch_height() } else { 0.0 };
    Some(Layout {
        origin_x,
        top,
        screen_w,
        notch: nh > 0.0,
        header: if nh > 0.0 { nh.max(30.0) } else { HEADER_H },
    })
}

/// Runs `f` on the main thread and waits a moment for it (window calls belong there).
fn on_main<F: FnOnce() + Send + 'static>(app: &AppHandle, f: F) {
    let (tx, rx) = std::sync::mpsc::sync_channel(1);
    let _ = app.run_on_main_thread(move || {
        f();
        let _ = tx.send(());
    });
    let _ = rx.recv_timeout(Duration::from_millis(800));
}

/// Puts the window (island size `w`×`h` plus its padding) centered at the top of the screen.
fn place(app: &AppHandle, lay: &Layout, w: f64, h: f64, show: bool) {
    let (ww, wh) = (w + 2.0 * PAD, h + PAD);
    let (x, y) = (lay.origin_x + (lay.screen_w - ww) / 2.0, lay.top);
    let app2 = app.clone();
    on_main(app, move || {
        let Some(win) = app2.get_webview_window("notch") else {
            return;
        };
        let _ = win.set_size(LogicalSize::new(ww, wh));
        let _ = win.set_position(LogicalPosition::new(x, y));
        if show {
            let _ = win.show();
        }
    });
    if let Ok(mut l) = live().lock() {
        l.w = w;
        l.h = h;
    }
}

fn open(app: &AppHandle, mode: &str, at_cursor: bool) {
    let gen = generation().fetch_add(1, Ordering::SeqCst) + 1;
    if let Err(e) = window(app) {
        return applog::line(format!("notch: could not create the window: {e}"));
    }
    let Some(lay) = layout(app, at_cursor) else {
        return applog::line("notch: no screen to anchor to");
    };
    let (w, h) = island(mode, lay.header);
    let (was_shown, cur) = live()
        .lock()
        .map(|l| (l.shown, (l.w, l.h)))
        .unwrap_or((false, (0.0, 0.0)));
    // while growing, the window is the bigger of what is on screen and what is coming
    let (cw, ch) = if was_shown {
        (cur.0.max(w), cur.1.max(h))
    } else {
        (w, h)
    };
    if let Ok(mut l) = live().lock() {
        l.mode = mode.to_string();
        l.shown = true;
        if !was_shown {
            l.focused = false;
        }
    }
    place(app, &lay, cw, ch, true);
    let lines = app.state::<AppState>().recent();
    let mut payload = info(&lay);
    payload["mode"] = json!(mode);
    payload["lines"] = json!(lines);
    let _ = app.emit_to("notch", "notch-show", payload);
    applog::line(format!("notch shown ({mode})"));
    thread::sleep(SETTLE);
    if generation().load(Ordering::SeqCst) == gen && (cw, ch) != (w, h) {
        place(app, &lay, w, h, false); // settled: don't keep a bigger transparent window over the screen
    }
}

/// Grows the island. `alert` is the small notification; anything else is the graph.
/// `at_cursor`: grow on the screen where the pointer is (a click on the tray icon).
pub fn present(app: &AppHandle, mode: &str, at_cursor: bool) {
    let mode = if mode == "alert" { "alert" } else { "graph" };
    let app = app.clone();
    let mode = mode.to_string();
    thread::spawn(move || open(&app, &mode, at_cursor));
}

/// Left click on the tray icon: open the graph, or close it if it's already the thing on screen.
pub fn toggle_graph(app: &AppHandle) {
    let open = live()
        .lock()
        .map(|l| l.shown && l.mode == "graph")
        .unwrap_or(false);
    if open {
        dismiss(app);
    } else {
        present(app, "graph", true);
    }
}

pub fn dismiss(app: &AppHandle) {
    let shown = live().lock().map(|l| l.shown).unwrap_or(false);
    if !shown {
        return;
    }
    let gen = generation().fetch_add(1, Ordering::SeqCst) + 1;
    let _ = app.emit_to("notch", "notch-hide", json!({}));
    let app = app.clone();
    thread::spawn(move || {
        thread::sleep(SETTLE);
        if generation().load(Ordering::SeqCst) != gen {
            return; // it was opened again meanwhile
        }
        let app2 = app.clone();
        on_main(&app, move || {
            if let Some(w) = app2.get_webview_window("notch") {
                let _ = w.hide();
            }
        });
        if let Ok(mut l) = live().lock() {
            l.shown = false;
            l.focused = false;
            l.mode.clear();
        }
        applog::line("notch hidden");
    });
}

pub fn state_json(app: &AppHandle, lines: Vec<String>) -> Value {
    let (mode, shown) = live()
        .lock()
        .map(|l| (l.mode.clone(), l.shown))
        .unwrap_or_default();
    let mut out = layout(app, false).map(|l| info(&l)).unwrap_or_else(|| {
        json!({ "os": std::env::consts::OS, "notch": false, "notch_w": NOTCH_W, "header": HEADER_H,
                "alert": [ALERT.0, HEADER_H + ALERT.1], "graph": [GRAPH.0, HEADER_H + GRAPH.1] })
    });
    out["mode"] = json!(if shown { mode } else { String::new() });
    out["lines"] = json!(lines);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slim_keeps_projects_and_drops_the_index() {
        let g = json!({
            "nodes": [
                {"id": "BRAIN.md", "label": "brain", "kind": "hub", "path": "BRAIN.md"},
                {"id": "projects/brain-project.md", "label": "brain-project", "kind": "project", "path": "projects/brain-project.md"},
                {"id": "projects/brain-project/historia.md", "label": "historia", "kind": "file", "path": "projects/brain-project/historia.md"},
                {"id": "tag:ia", "label": "#ia", "kind": "tag", "path": null},
                {"id": "dir:projects", "label": "projects/", "kind": "folder", "path": "projects"}
            ],
            "links": [
                {"source": "BRAIN.md", "target": "dir:projects", "kind": "folder"},
                {"source": "projects/brain-project/historia.md", "target": "projects/brain-project.md", "kind": "part"},
                {"source": "projects/brain-project.md", "target": "tag:ia", "kind": "tag"}
            ]
        });
        let out = slim_graph(&g);
        let ids: Vec<&str> = out["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|n| n["id"].as_str())
            .collect();
        assert_eq!(
            ids,
            vec![
                "projects/brain-project.md",
                "projects/brain-project/historia.md"
            ]
        );
        assert_eq!(out["links"].as_array().unwrap().len(), 1);
    }
}
