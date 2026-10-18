//! The notch panel: a small dark window at the top center with shortcuts to the dashboard, the
//! graph and the chat. It opens when the vault changes (and from the tray menu); the page
//! (`ui/notch.html`) hides itself after a few seconds unless the pointer is over it.

use crate::{applog, AppState};
use tauri::{AppHandle, Emitter, LogicalPosition, Manager, WebviewUrl, WebviewWindowBuilder};

const W: f64 = 380.0;
const H: f64 = 190.0;

fn window(app: &AppHandle) -> Result<tauri::WebviewWindow, String> {
    if let Some(w) = app.get_webview_window("notch") {
        return Ok(w);
    }
    WebviewWindowBuilder::new(app, "notch", WebviewUrl::App("notch.html".into()))
        .title("brain")
        .inner_size(W, H)
        .decorations(false)
        .resizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .focused(false)
        .visible(false)
        .shadow(true)
        .build()
        .map_err(|e| e.to_string())
}

pub fn show(app: &AppHandle) {
    let app = app.clone();
    // build() waits for the main thread: never call it from there, so it always runs on its own thread
    std::thread::spawn(move || {
        let w = match window(&app) {
            Ok(w) => w,
            Err(e) => return applog::line(format!("notch: could not create the window: {e}")),
        };
        let lines = app.state::<AppState>().recent();
        let _ = app.emit_to("notch", "notch-update", lines);
        if let Ok(Some(m)) = w.current_monitor().or_else(|_| app.primary_monitor()) {
            let scale = m.scale_factor();
            let screen_w = m.size().width as f64 / scale;
            let origin = m.position().x as f64 / scale;
            let _ = w.set_position(LogicalPosition::new(origin + (screen_w - W) / 2.0, 0.0));
        }
        let _ = w.show();
        applog::line("notch panel shown");
    });
}

pub fn hide(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("notch") {
        let _ = w.hide();
    }
}
