//! Keeps the app's own binary in step with the brain code.
//!
//! "Update and restart" in the dashboard (and `brain update`) pulls the new Python and reopens the
//! dashboard, but the tray icon, the notch and the notifications are this binary: it stays on the old
//! version until it is replaced. So the watcher compares the version the dashboard reports with the one
//! compiled into the app; when the code is newer, the app asks `brain app-update` to download the latest
//! release, install it and reopen, and quits so the whole thing (icon included) restarts on the new
//! version instead of leaving an old icon next to a new window.

use crate::{applog, backend::Backend};
use std::time::{SystemTime, UNIX_EPOCH};

/// Don't try the same target again for this long (the release may not carry the app yet).
const RETRY_AFTER_SECS: u64 = 6 * 3600;

/// `v0.03.3.3` → `[0, 3, 3, 3]`; `0.3.3-2` → `[0, 3, 3, 2]`; anything that isn't a number is ignored.
fn parts(v: &str) -> Vec<u64> {
    let mut p: Vec<u64> = v
        .trim()
        .trim_start_matches('v')
        .split(['.', '-'])
        .filter_map(|x| x.parse().ok())
        .collect();
    p.resize(4.max(p.len()), 0);
    p
}

/// The version the code on disk reports (`VERSION`) is newer than this binary.
pub fn is_outdated(own: &str, dashboard: &str) -> bool {
    let (own, dash) = (parts(own), parts(dashboard));
    !dash.iter().all(|n| *n == 0) && dash > own
}

pub fn own_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// What a previous attempt left (`data/app_update.json`): the version it was going for and when.
fn recent_attempt(be: &Backend, target: &str) -> bool {
    let path = be.home.join("data").join("app_update.json");
    let Ok(text) = std::fs::read_to_string(path) else {
        return false;
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else {
        return false;
    };
    v["target"].as_str() == Some(target)
        && now().saturating_sub(v["at"].as_u64().unwrap_or(0)) < RETRY_AFTER_SECS
}

fn remember_attempt(be: &Backend, target: &str) {
    let dir = be.home.join("data");
    let _ = std::fs::create_dir_all(&dir);
    let body = serde_json::json!({ "target": target, "at": now() });
    let _ = std::fs::write(dir.join("app_update.json"), body.to_string());
}

/// True when an update was started and the app should quit now.
pub fn check(be: &Backend, dashboard: &str) -> bool {
    if !is_outdated(own_version(), dashboard) {
        return false;
    }
    if std::env::var_os("BRAIN_NO_APP_UPDATE").is_some() {
        return false;
    }
    // a build run from a checkout (`cargo run`) is the developer's own: never replace it
    let from_build = std::env::current_exe()
        .map(|p| p.components().any(|c| c.as_os_str() == "target"))
        .unwrap_or(false);
    if from_build {
        return false;
    }
    if recent_attempt(be, dashboard) {
        return false;
    }
    remember_attempt(be, dashboard);
    applog::line(format!(
        "this app is {} but brain is {dashboard}: updating the app",
        own_version()
    ));
    match be.spawn_app_update() {
        Ok(()) => true,
        Err(e) => {
            applog::line(format!("could not start the app update: {e}"));
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_code_is_newer_than_the_app() {
        assert!(is_outdated("0.3.3-2", "v0.03.3.3"));
        assert!(is_outdated("0.3.3", "v0.03.3.1"));
        assert!(is_outdated("0.3.3-3", "v0.03.4"));
    }

    #[test]
    fn same_older_or_unreadable_versions_do_not_update() {
        assert!(!is_outdated("0.3.3-3", "v0.03.3.3"));
        assert!(!is_outdated("0.3.3-3", "v0.03.3.2"));
        assert!(!is_outdated("0.3.3-3", "v0.03.3")); // 0.3.3 < 0.3.3-3 in release order
        assert!(!is_outdated("0.3.3-3", ""));
    }
}
