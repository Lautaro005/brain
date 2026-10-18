//! brain desktop: a shell around the brain dashboard. It starts the dashboard (Python, installed by
//! install.sh / install.ps1) if nobody did, shows it in a window and keeps running from the menu bar
//! or tray when the window is closed, with system notifications and a notch panel for vault changes.
//! It writes its own log to data/logs/app.log (the dashboard shows it in Logs → App).

mod applog;
mod backend;
mod notch;
mod watch;

use backend::Backend;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_opener::OpenerExt;

const VIEWS: [&str; 10] = [
    "panel",
    "chat",
    "perfil",
    "agentes",
    "conexiones",
    "grafo",
    "conocimiento",
    "factcheck",
    "logs",
    "ajustes",
];

pub struct AppState {
    pub backend: Arc<Backend>,
    pub ready: AtomicBool,
    pub close_to_tray: AtomicBool,
    recent: Mutex<Vec<String>>,
    title: Mutex<Option<MenuItem<tauri::Wry>>>,
}

impl AppState {
    pub fn remember(&self, lines: &[String]) {
        let mut r = self.recent.lock().unwrap();
        for l in lines {
            r.extend(l.lines().map(str::to_string));
        }
        let n = r.len();
        if n > 4 {
            r.drain(..n - 4);
        }
    }
    pub fn recent(&self) -> Vec<String> {
        self.recent.lock().unwrap().iter().rev().cloned().collect()
    }
}

// ---------- windows ----------

fn main_window(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    app.get_webview_window("main")
}

/// Shows the dashboard window; `view` is a dashboard view (`#grafo`…), `js` runs after it is on screen.
fn show_main(app: &AppHandle, view: Option<&str>, js: Option<&str>) {
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    let Some(w) = main_window(app) else { return };
    let _ = w.show();
    let _ = w.unminimize();
    let _ = w.set_focus();
    if !app.state::<AppState>().ready.load(Ordering::SeqCst) {
        return;
    }
    if let Some(v) = view.filter(|v| VIEWS.contains(v)) {
        let _ = w.eval(format!("location.hash = '#{v}'"));
    }
    if let Some(js) = js {
        let _ = w.eval(format!("setTimeout(function(){{ {js} }}, 400)"));
    }
}

fn hide_main(app: &AppHandle) {
    if let Some(w) = main_window(app) {
        let _ = w.hide();
    }
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
}

fn fail(app: &AppHandle, msg: &str) {
    applog::line(format!("start failed: {msg}"));
    if let Some(w) = main_window(app) {
        let enc: String = msg
            .bytes()
            .map(|b| {
                if b.is_ascii_alphanumeric() {
                    (b as char).to_string()
                } else {
                    format!("%{b:02X}")
                }
            })
            .collect();
        let _ = w.eval(format!("location.replace('error.html#{enc}')"));
    }
}

/// Starts the dashboard (if needed), waits for it and loads it in the window.
fn start_dashboard(app: AppHandle) {
    std::thread::spawn(move || {
        let state = app.state::<AppState>();
        let be = state.backend.clone();
        if let Err(e) = be.ensure_running() {
            return fail(&app, &e);
        }
        if !be.wait_ready(Duration::from_secs(180)) {
            return fail(&app, "the dashboard did not start. Open Logs (App) or run `brain` in a terminal to see why");
        }
        state.ready.store(true, Ordering::SeqCst);
        let version = be.version();
        applog::line(format!("dashboard ready at {} (brain {version})", be.url()));
        if let Some(item) = state.title.lock().unwrap().as_ref() {
            let _ = item.set_text(format!("brain {version}"));
        }
        if let Some(w) = main_window(&app) {
            let _ = w.eval(format!("location.replace('{}/')", be.url()));
        }
    });
}

fn is_local(url: &Url) -> bool {
    matches!(url.scheme(), "tauri" | "about" | "data" | "blob")
        || (url.scheme() == "http"
            && matches!(
                url.host_str(),
                Some("127.0.0.1" | "localhost" | "tauri.localhost" | "ipc.localhost")
            ))
        || (url.scheme() == "https" && url.host_str() == Some("tauri.localhost"))
}

fn create_main(app: &AppHandle) -> tauri::Result<()> {
    let opener = app.clone();
    let opener2 = app.clone();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("brain")
        .inner_size(1280.0, 820.0)
        .min_inner_size(900.0, 600.0)
        .center()
        .initialization_script("window.__BRAIN_APP__ = true;")
        // links to other sites (GitHub, docs, obsidian://) open in the system's browser or app
        .on_navigation(move |url| {
            if is_local(url) {
                return true;
            }
            applog::line(format!("opening outside the app: {url}"));
            let _ = opener.opener().open_url(url.as_str(), None::<&str>);
            false
        })
        .on_new_window(move |url, _| {
            applog::line(format!(
                "new window requested, opening outside the app: {url}"
            ));
            let _ = opener2.opener().open_url(url.as_str(), None::<&str>);
            tauri::webview::NewWindowResponse::Deny
        })
        .build()?;
    Ok(())
}

// ---------- tray ----------

fn quit(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        applog::line("quit requested");
        let state = app.state::<AppState>();
        state.ready.store(false, Ordering::SeqCst);
        state.backend.stop();
        app.exit(0);
    });
}

fn build_tray(app: &AppHandle, version: &str) -> tauri::Result<()> {
    let title = MenuItem::with_id(
        app,
        "title",
        format!("brain {version}"),
        false,
        None::<&str>,
    )?;
    let open = MenuItem::with_id(app, "open", "Open", true, None::<&str>)?;
    let notifications =
        MenuItem::with_id(app, "notifications", "Notifications", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings", true, Some("CmdOrCtrl+,"))?;
    let updates = MenuItem::with_id(app, "updates", "Updates", true, None::<&str>)?;
    let notch = MenuItem::with_id(app, "notch", "Notch…", true, None::<&str>)?;
    let close = MenuItem::with_id(app, "close", "Close", true, Some("CmdOrCtrl+Q"))?;
    let menu = Menu::with_items(
        app,
        &[
            &title,
            &PredefinedMenuItem::separator(app)?,
            &open,
            &notifications,
            &settings,
            &updates,
            &notch,
            &PredefinedMenuItem::separator(app)?,
            &close,
        ],
    )?;
    *app.state::<AppState>().title.lock().unwrap() = Some(title);
    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().cloned().expect("bundle icon"))
        .tooltip("brain")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| {
            applog::line(format!("tray: {}", event.id.as_ref()));
            match event.id.as_ref() {
                "open" => show_main(app, None, None),
                "notifications" => show_main(
                    app,
                    Some("ajustes"),
                    Some("var c=document.querySelector('#notify-card'); if(c) c.scrollIntoView({block:'center'});"),
                ),
                "settings" => show_main(app, Some("ajustes"), None),
                "updates" => show_main(
                    app,
                    Some("ajustes"),
                    Some("var b=document.querySelector('#update-btn'); if(b) b.click();"),
                ),
                "notch" => notch::show(app),
                "close" => quit(app),
                _ => {}
            }
        })
        .build(app)?;
    Ok(())
}

// ---------- commands (the notch page and the dashboard call these) ----------

#[tauri::command]
fn test_notification(app: AppHandle) {
    watch::show(&app, "brain", watch::test_body());
}

#[tauri::command]
fn open_view(app: AppHandle, view: String) {
    show_main(&app, Some(&view), None);
}

#[tauri::command]
fn hide_notch(app: AppHandle) {
    notch::hide(&app);
}

#[tauri::command]
fn notch_lines(state: tauri::State<AppState>) -> Vec<String> {
    state.recent()
}

#[tauri::command]
fn retry_start(app: AppHandle) {
    if let Some(w) = main_window(&app) {
        let _ = w.eval("location.replace('index.html')");
    }
    start_dashboard(app);
}

pub fn run() {
    if std::env::args().any(|a| a == "--version" || a == "-V") {
        println!("brain-app {}", env!("CARGO_PKG_VERSION"));
        return;
    }

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main(app, None, None)
        }))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let handle = app.handle().clone();
            let backend = match Backend::discover() {
                Ok(b) => Arc::new(b),
                Err(e) => {
                    // not installed: the window still opens, with what to run
                    applog::init(std::env::temp_dir().join("brain-app.log"));
                    applog::line(&e);
                    app.manage(state_for(Arc::new(Backend::placeholder())));
                    create_main(&handle)?;
                    build_tray(&handle, "")?;
                    fail(&handle, &e);
                    return Ok(());
                }
            };
            applog::init(backend.home.join("data").join("logs").join("app.log"));
            applog::line(format!(
                "brain app {} starting (home {}, port {})",
                env!("CARGO_PKG_VERSION"),
                backend.home.display(),
                backend.port
            ));
            app.manage(state_for(backend.clone()));
            create_main(&handle)?;
            let version = std::fs::read_to_string(backend.home.join("VERSION")).unwrap_or_default();
            build_tray(&handle, version.trim())?;
            start_dashboard(handle.clone());
            watch::spawn(handle.clone());
            quit_on_signals(handle);
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            if let WindowEvent::CloseRequested { api, .. } = event {
                let app = window.app_handle();
                if app.state::<AppState>().close_to_tray.load(Ordering::SeqCst) {
                    api.prevent_close();
                    hide_main(app);
                    applog::line("window closed: brain keeps running in the tray");
                } else {
                    quit(app);
                    api.prevent_close();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            test_notification,
            open_view,
            hide_notch,
            notch_lines,
            retry_start
        ])
        .build(tauri::generate_context!())
        .expect("error while building the brain app");

    app.run(|app, event| match event {
        // the last window hiding must not end the app: it lives in the tray until Close
        RunEvent::ExitRequested { api, code, .. } if code.is_none() => api.prevent_exit(),
        RunEvent::Exit => app.state::<AppState>().backend.stop(),
        _ => {}
    });
}

/// SIGTERM / SIGINT / SIGHUP (logout, `kill`, a closed terminal) end the app the same way as Close:
/// the dashboard the app started is stopped too instead of being left running.
#[cfg(unix)]
fn quit_on_signals(app: AppHandle) {
    use signal_hook::consts::{SIGHUP, SIGINT, SIGTERM};
    let Ok(mut signals) = signal_hook::iterator::Signals::new([SIGTERM, SIGINT, SIGHUP]) else {
        return;
    };
    std::thread::spawn(move || {
        if let Some(sig) = signals.forever().next() {
            applog::line(format!("signal {sig}: closing"));
            quit(&app);
        }
    });
}

#[cfg(not(unix))]
fn quit_on_signals(_app: AppHandle) {}

fn state_for(backend: Arc<Backend>) -> AppState {
    AppState {
        backend,
        ready: AtomicBool::new(false),
        close_to_tray: AtomicBool::new(true),
        recent: Mutex::new(Vec::new()),
        title: Mutex::new(None),
    }
}
