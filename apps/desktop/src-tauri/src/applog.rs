//! The app's own log: `<brain>/data/logs/app.log`. The dashboard shows it in Logs ("App"), next to
//! the services it manages. It also gets the dashboard's stdout/stderr when the app started it.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

const MAX_BYTES: u64 = 1_000_000;

static FILE: Mutex<Option<PathBuf>> = Mutex::new(None);

pub fn init(path: PathBuf) {
    if let Some(dir) = path.parent() {
        let _ = fs::create_dir_all(dir);
    }
    if fs::metadata(&path)
        .map(|m| m.len() > MAX_BYTES)
        .unwrap_or(false)
    {
        let _ = fs::rename(&path, path.with_extension("log.1"));
    }
    *FILE.lock().unwrap() = Some(path);
}

/// One line with a local timestamp. Never fails: a log must not take the app down.
pub fn line(msg: impl AsRef<str>) {
    let stamp = chrono::Local::now().format("%Y-%m-%d %H:%M:%S");
    let text = format!("{stamp} [app] {}", msg.as_ref());
    write(&text);
}

/// A line of the dashboard's own output, as it came.
pub fn dashboard(msg: &str) {
    write(&format!("[dashboard] {msg}"));
}

fn write(text: &str) {
    eprintln!("{text}");
    if let Some(path) = FILE.lock().unwrap().as_ref() {
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
            let _ = writeln!(f, "{text}");
        }
    }
}
