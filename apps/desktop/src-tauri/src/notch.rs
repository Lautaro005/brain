//! Notch panel: a black island that grows out of the top center of the screen (on a Mac, out of the
//! notch — the same black, so the seam disappears). Two sizes:
//! - `alert`: a short strip when the vault changes (Settings → notch, not a system notification)
//! - `graph`: the vault graph, opened by a left click on the tray icon or by tapping the alert
//!
//! The page is `ui/notch.html`. It has no network access; the graph arrives through `notch_graph`.

use crate::{applog, AppState};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, WebviewUrl, WebviewWindowBuilder};

const SEED_W: f64 = 188.0;
const SEED_H: f64 = 34.0;
const ALERT_W: f64 = 308.0;
const ALERT_H: f64 = 76.0;
const GRAPH_W: f64 = 460.0;
const GRAPH_H: f64 = 312.0;

struct Live {
    mode: String,
    w: f64,
    h: f64,
    shown: bool,
    /// True only after the island itself has been focused, so the unfocused show doesn't immediately close it.
    focused: bool,
}

fn live() -> &'static Mutex<Live> {
    static LIVE: Mutex<Live> = Mutex::new(Live {
        mode: String::new(), w: SEED_W, h: SEED_H, shown: false, focused: false,
    });
    &LIVE
}

fn generation() -> &'static AtomicU64 {
    static GEN: AtomicU64 = AtomicU64::new(0);
    &GEN
}

fn target(mode: &str) -> (f64, f64) {
    if mode == "alert" { (ALERT_W, ALERT_H) } else { (GRAPH_W, GRAPH_H) }
}

fn ease(t: f64) -> f64 {
    let t = 1.0 - t;
    1.0 - t * t * t
}

/// Drops tag, entity, folder and index nodes so the island stays readable, and caps the rest.
pub fn slim_graph(graph: &Value) -> Value {
    let keep = |k: &str| {
        matches!(k, "project" | "subproject" | "file" | "memory" | "skill" | "profile" | "source" | "note")
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
    let mut picked: Vec<&Value> = nodes.iter().filter(|n| keep(n["kind"].as_str().unwrap_or(""))).collect();
    picked.sort_by(|a, b| {
        let rank = |k: &str| match k {
            "project" | "profile" => 0,
            "subproject" | "skill" | "memory" => 1,
            _ => 2,
        };
        rank(a["kind"].as_str().unwrap_or("")).cmp(&rank(b["kind"].as_str().unwrap_or("")))
            .then(degree.get(b["id"].as_str().unwrap_or("")).unwrap_or(&0)
                .cmp(degree.get(a["id"].as_str().unwrap_or("")).unwrap_or(&0)))
    });
    picked.truncate(42);
    let ids: std::collections::HashSet<&str> = picked.iter().filter_map(|n| n["id"].as_str()).collect();
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
        .inner_size(SEED_W, SEED_H)
        .decorations(false)
        .transparent(true)
        .resizable(false)
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
            let was = live().lock().map(|mut l| {
                let prev = l.focused;
                l.focused = *focused;
                prev && !focused && l.shown && l.mode == "graph"
            }).unwrap_or(false);
            if was {
                let app = app_for_blur.clone();
                thread::spawn(move || dismiss(&app));
            }
        }
    });
    lift_over_menu_bar(app, &w);
    Ok(w)
}

/// On macOS the island has to paint *over* the menu bar, in the notch's own black. Below the bar it
/// reads as a square stuck underneath, which is what this replaces.
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
        let _: () = msg_send![ns_window, setHasShadow: true];
        let _: () = msg_send![ns_window, setHidesOnDeactivate: false];
        let color: *mut AnyObject = msg_send![class!(NSColor), clearColor];
        let _: () = msg_send![ns_window, setBackgroundColor: color];
    });
}

#[cfg(not(target_os = "macos"))]
fn lift_over_menu_bar(_app: &AppHandle, _window: &tauri::WebviewWindow) {}

struct Frame {
    origin_x: f64,
    top: f64,
    screen_w: f64,
}

fn frame_of(app: &AppHandle) -> Option<Frame> {
    let w = app.get_webview_window("notch")?;
    let m = w.current_monitor().ok().flatten().or_else(|| w.primary_monitor().ok().flatten())?;
    let scale = m.scale_factor();
    let origin_x = m.position().x as f64 / scale;
    let screen_w = m.size().width as f64 / scale;
    // macOS: the physical top, so the flat black edge hides inside the notch. Elsewhere: below
    // whatever panel the system already put at the top.
    let top = {
        #[cfg(target_os = "macos")]
        { m.position().y as f64 / scale }
        #[cfg(not(target_os = "macos"))]
        { m.work_area().position.y as f64 / scale }
    };
    Some(Frame { origin_x, top, screen_w })
}

fn place(app: &AppHandle, frame: &Frame, w: f64, h: f64) {
    let Some(win) = app.get_webview_window("notch") else { return };
    let _ = win.set_size(LogicalSize::new(w, h));
    let _ = win.set_position(LogicalPosition::new(frame.origin_x + (frame.screen_w - w) / 2.0, frame.top));
    if let Ok(mut live) = live().lock() {
        live.w = w;
        live.h = h;
    }
}

fn animate(app: &AppHandle, gen: u64, frame: &Frame, from: (f64, f64), to: (f64, f64)) {
    const STEPS: i32 = 13;
    for i in 1..=STEPS {
        if generation().load(Ordering::SeqCst) != gen {
            return;
        }
        let t = ease(i as f64 / STEPS as f64);
        let w = from.0 + (to.0 - from.0) * t;
        let h = from.1 + (to.1 - from.1) * t;
        let app = app.clone();
        let frame = Frame { origin_x: frame.origin_x, top: frame.top, screen_w: frame.screen_w };
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        let _ = app.clone().run_on_main_thread(move || {
            place(&app, &frame, w, h);
            let _ = tx.send(());
        });
        let _ = rx.recv_timeout(Duration::from_millis(400));
        thread::sleep(Duration::from_millis(12));
    }
}

fn spawn_ui(app: AppHandle, mode: String, hide_after: bool) {
    let gen = generation().fetch_add(1, Ordering::SeqCst) + 1;
    thread::spawn(move || {
        if let Err(e) = window(&app) {
            return applog::line(format!("notch: could not create the window: {e}"));
        }
        let Some(frame) = frame_of(&app) else {
            return applog::line("notch: no screen to anchor to");
        };
        let lines = app.state::<AppState>().recent();
        if !hide_after {
            let _ = app.emit_to("notch", "notch-show", json!({ "mode": mode, "lines": lines }));
        }
        let (from, to) = {
            let cur = live().lock().ok();
            let shown = cur.as_ref().map(|l| l.shown).unwrap_or(false);
            let start = if shown || hide_after {
                cur.map(|l| (l.w, l.h)).unwrap_or((SEED_W, SEED_H))
            } else {
                (SEED_W, SEED_H)
            };
            let end = if hide_after { (SEED_W, SEED_H) } else { target(&mode) };
            (start, end)
        };
        if !hide_after {
            if let Ok(mut l) = live().lock() {
                l.mode = mode.clone();
                l.shown = true;
                l.focused = false;
            }
            let _ = app.run_on_main_thread({
                let app = app.clone();
                let frame = Frame { origin_x: frame.origin_x, top: frame.top, screen_w: frame.screen_w };
                move || {
                    place(&app, &frame, from.0, from.1);
                    if let Some(w) = app.get_webview_window("notch") {
                        let _ = w.show();
                    }
                }
            });
            thread::sleep(Duration::from_millis(16));
        }
        animate(&app, gen, &frame, from, to);
        if generation().load(Ordering::SeqCst) != gen {
            return;
        }
        if hide_after {
            let app_h = app.clone();
            let _ = app.run_on_main_thread(move || {
                if let Some(w) = app_h.get_webview_window("notch") {
                    let _ = w.hide();
                }
            });
            if let Ok(mut l) = live().lock() {
                l.shown = false;
                l.focused = false;
                l.mode.clear();
                l.w = SEED_W;
                l.h = SEED_H;
            }
            applog::line("notch hidden");
        } else {
            let _ = app.emit_to("notch", "notch-settled", json!({ "mode": mode }));
            applog::line(format!("notch shown ({mode})"));
        }
    });
}

/// Grows the island. `alert` is the small notification; anything else is the graph.
pub fn present(app: &AppHandle, mode: &str) {
    let mode = if mode == "alert" { "alert" } else { "graph" };
    if let Ok(l) = live().lock() {
        if l.shown && l.mode == mode {
            let lines = app.state::<AppState>().recent();
            let _ = app.emit_to("notch", "notch-show", json!({ "mode": mode, "lines": lines }));
            return;
        }
    }
    spawn_ui(app.clone(), mode.to_string(), false);
}

/// Left click on the tray icon: open the graph, or close it if it's already the thing on screen.
pub fn toggle_graph(app: &AppHandle) {
    let open = live().lock().map(|l| l.shown && l.mode == "graph").unwrap_or(false);
    if open { dismiss(app); } else { present(app, "graph"); }
}

pub fn dismiss(app: &AppHandle) {
    let shown = live().lock().map(|l| l.shown).unwrap_or(false);
    if !shown {
        return;
    }
    spawn_ui(app.clone(), "alert".to_string(), true);
}

pub fn state_json(lines: Vec<String>) -> Value {
    let (mode, shown) = live().lock().map(|l| (l.mode.clone(), l.shown)).unwrap_or_default();
    json!({
        "mode": if shown { mode } else { String::new() },
        "lines": lines,
        "os": std::env::consts::OS,
    })
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
        let ids: Vec<&str> = out["nodes"].as_array().unwrap().iter().filter_map(|n| n["id"].as_str()).collect();
        assert_eq!(ids, vec!["projects/brain-project.md", "projects/brain-project/historia.md"]);
        assert_eq!(out["links"].as_array().unwrap().len(), 1);
    }
}
