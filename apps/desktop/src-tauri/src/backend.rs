//! The Python side: finds the brain install, starts the dashboard if nobody did, talks to its API
//! and stops it on quit. The app never touches the vault: everything goes through the dashboard.

use crate::applog;
use serde_json::Value;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const DEFAULT_PORT: u16 = 8765;

pub struct Backend {
    pub home: PathBuf,
    pub port: u16,
    child: Mutex<Option<Child>>,
    /// True only if this app launched the dashboard (then, and only then, quitting stops it).
    started_here: AtomicBool,
}

fn user_home() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn is_brain(dir: &Path) -> bool {
    dir.join("dashboard.py").is_file()
}

/// `BRAIN_HOME`, then `~/.brain` (where install.sh / install.ps1 put it), then a checkout above the executable.
pub fn find_home() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(p) = std::env::var_os("BRAIN_HOME") {
        candidates.push(PathBuf::from(p));
    }
    if let Some(h) = user_home() {
        candidates.push(h.join(".brain"));
    }
    if let Ok(exe) = std::env::current_exe() {
        candidates.extend(exe.ancestors().skip(1).map(Path::to_path_buf));
    }
    candidates.into_iter().find(|d| is_brain(d))
}

/// Variables the AppImage runtime sets for its own bundled libraries. If the dashboard inherited them,
/// Python would look for its standard library inside the AppImage (`No module named 'encodings'`) and
/// every tool it starts (Chroma, Ollama, git) would load the AppImage's libraries.
const APPIMAGE_VARS: [&str; 22] = [
    "APPDIR",
    "APPIMAGE",
    "ARGV0",
    "OWD",
    "APPIMAGE_EXTRACT_AND_RUN",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PERLLIB",
    "QT_PLUGIN_PATH",
    "GST_PLUGIN_SYSTEM_PATH",
    "GST_PLUGIN_SYSTEM_PATH_1_0",
    "GTK_DATA_PREFIX",
    "GTK_EXE_PREFIX",
    "GTK_IM_MODULE_FILE",
    "GTK_PATH",
    "GTK_THEME",
    "GI_TYPELIB_PATH",
    "GIO_MODULE_DIR",
    "GSETTINGS_SCHEMA_DIR",
    "GDK_PIXBUF_MODULE_FILE",
    "GST_REGISTRY_1_0",
];

/// `var` without the entries that point inside the AppImage (`None` if nothing is left).
fn without_appdir(var: &str, appdir: &std::ffi::OsStr) -> Option<std::ffi::OsString> {
    let value = std::env::var_os(var)?;
    let kept: Vec<PathBuf> = std::env::split_paths(&value)
        .filter(|p| !p.starts_with(appdir))
        .collect();
    if kept.is_empty() {
        None
    } else {
        std::env::join_paths(kept).ok()
    }
}

/// Gives the child the environment the user's shell would have, not the AppImage's.
fn clean_appimage_env(cmd: &mut Command) {
    let Some(appdir) = std::env::var_os("APPDIR") else {
        return;
    };
    for v in APPIMAGE_VARS {
        cmd.env_remove(v);
    }
    for v in ["LD_LIBRARY_PATH", "XDG_DATA_DIRS"] {
        match without_appdir(v, &appdir) {
            Some(value) => cmd.env(v, value),
            None => cmd.env_remove(v),
        };
    }
}

/// A GUI app starts with a bare PATH (macOS especially): add where uv, git and ollama usually live.
fn fixed_path() -> std::ffi::OsString {
    let mut parts: Vec<PathBuf> = Vec::new();
    if let Some(h) = user_home() {
        parts.push(h.join(".local/bin"));
        parts.push(h.join(".cargo/bin"));
    }
    for p in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] {
        parts.push(PathBuf::from(p));
    }
    let own = std::env::var_os("APPDIR");
    let from_appimage = |p: &PathBuf| own.as_deref().is_some_and(|d| p.starts_with(d));
    if let Some(cur) = std::env::var_os("PATH") {
        parts.extend(std::env::split_paths(&cur).filter(|p| !from_appimage(p)));
    }
    std::env::join_paths(parts).unwrap_or_default()
}

impl Backend {
    pub fn discover() -> Result<Backend, String> {
        let home = find_home().ok_or_else(|| {
            "brain is not installed on this computer (no dashboard.py in ~/.brain or BRAIN_HOME)".to_string()
        })?;
        let port = std::env::var("BRAIN_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
            .unwrap_or(DEFAULT_PORT);
        Ok(Backend {
            home,
            port,
            child: Mutex::new(None),
            started_here: AtomicBool::new(false),
        })
    }

    /// For when brain is not installed: nothing will be started from it.
    pub fn placeholder() -> Backend {
        Backend {
            home: PathBuf::new(),
            port: DEFAULT_PORT,
            child: Mutex::new(None),
            started_here: AtomicBool::new(false),
        }
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    fn agent(&self) -> ureq::Agent {
        ureq::AgentBuilder::new()
            .timeout(Duration::from_secs(4))
            .build()
    }

    pub fn get_json(&self, path: &str) -> Result<Value, String> {
        self.agent()
            .get(&format!("{}{path}", self.url()))
            .call()
            .map_err(|e| e.to_string())?
            .into_json::<Value>()
            .map_err(|e| e.to_string())
    }

    fn post_quit(&self) -> Result<(), String> {
        self.agent()
            .post(&format!("{}/api/quit", self.url()))
            .set("X-Brain", "1")
            .send_string("{}")
            .map(|_| ())
            .map_err(|e| e.to_string())
    }

    pub fn healthy(&self) -> bool {
        self.get_json("/api/version").is_ok()
    }

    pub fn version(&self) -> String {
        self.get_json("/api/version")
            .ok()
            .and_then(|v| v["current"].as_str().map(str::to_string))
            .or_else(|| {
                std::fs::read_to_string(self.home.join("VERSION"))
                    .ok()
                    .map(|s| s.trim().to_string())
            })
            .unwrap_or_default()
    }

    /// Starts the dashboard unless one already answers on the port (then it's left alone).
    pub fn ensure_running(&self) -> Result<(), String> {
        if self.healthy() {
            applog::line(format!(
                "dashboard already running on port {}: using it",
                self.port
            ));
            return Ok(());
        }
        let mut cmd = self.command();
        clean_appimage_env(&mut cmd);
        cmd.current_dir(&self.home)
            .env("BRAIN_APP", "1")
            .env("PATH", fixed_path())
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        applog::line(format!(
            "starting the dashboard from {}",
            self.home.display()
        ));
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("could not start brain: {e}"))?;
        for stream in [
            child
                .stdout
                .take()
                .map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
            child
                .stderr
                .take()
                .map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
        ]
        .into_iter()
        .flatten()
        {
            std::thread::spawn(move || {
                for line in BufReader::new(stream).lines().map_while(Result::ok) {
                    applog::dashboard(&line);
                }
            });
        }
        self.started_here.store(true, Ordering::SeqCst);
        *self.child.lock().unwrap() = Some(child);
        Ok(())
    }

    #[cfg(unix)]
    fn command(&self) -> Command {
        use std::os::unix::process::CommandExt;
        let mut c = Command::new("/bin/bash");
        c.arg(self.home.join("brain.sh"))
            .args(["--no-browser", "--port", &self.port.to_string()])
            .process_group(0); // own group: stopping it takes brain.sh, Python and what it started
        c
    }

    #[cfg(windows)]
    fn command(&self) -> Command {
        use std::os::windows::process::CommandExt;
        let mut c = Command::new("powershell.exe");
        c.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(self.home.join("brain.ps1"))
            .args(["--no-browser", "--port", &self.port.to_string()])
            .creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        c
    }

    /// Waits until the dashboard answers; false if it dies or `limit` passes.
    pub fn wait_ready(&self, limit: Duration) -> bool {
        let start = Instant::now();
        while start.elapsed() < limit {
            if self.healthy() {
                return true;
            }
            if let Some(child) = self.child.lock().unwrap().as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    applog::line(format!("the dashboard exited early ({status})"));
                    return false;
                }
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        false
    }

    /// Quit: ask the dashboard to close (it stops what it started), then make sure nothing is left.
    pub fn stop(&self) {
        if !self.started_here.swap(false, Ordering::SeqCst) {
            return;
        }
        applog::line("stopping the dashboard");
        let _ = self.post_quit();
        let mut guard = self.child.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            if !wait_exit(child, Duration::from_secs(12)) {
                applog::line("it did not close in time: ending the process group");
                kill_tree(child.id());
                if !wait_exit(child, Duration::from_secs(5)) {
                    let _ = child.kill();
                }
            }
        }
        *guard = None;
    }
}

fn wait_exit(child: &mut Child, limit: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < limit {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

#[cfg(unix)]
fn kill_tree(pid: u32) {
    let _ = Command::new("kill")
        .args(["-TERM", &format!("-{pid}")])
        .status();
}

#[cfg(windows)]
fn kill_tree(pid: u32) {
    use std::os::windows::process::CommandExt;
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(0x0800_0000)
        .status();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn appimage_entries_are_dropped_from_path_like_variables() {
        let app = PathBuf::from("appdir_x");
        let join = |parts: &[PathBuf]| std::env::join_paths(parts).unwrap();
        std::env::set_var(
            "BRAIN_TEST_PATHS",
            join(&[
                app.join("usr").join("lib"),
                PathBuf::from("keep_me"),
                app.join("usr").join("bin"),
            ]),
        );
        let kept = without_appdir("BRAIN_TEST_PATHS", app.as_os_str()).unwrap();
        assert_eq!(kept, join(&[PathBuf::from("keep_me")]));
        std::env::set_var("BRAIN_TEST_ONLY", join(&[app.join("usr")]));
        assert!(without_appdir("BRAIN_TEST_ONLY", app.as_os_str()).is_none());
        assert!(without_appdir("BRAIN_TEST_MISSING", app.as_os_str()).is_none());
    }
}
