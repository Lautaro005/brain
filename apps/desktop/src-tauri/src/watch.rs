//! Watches the vault through the dashboard (`/api/activity`, every 5 s) and tells the user:
//! a system notification, a `●` on the tray icon and the notch panel. Preferences come from
//! `/api/app` (Settings → Notifications in the dashboard), so they apply with the window closed.

use crate::{applog, notch, AppState};
use serde_json::Value;
use std::sync::atomic::Ordering;
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

const POLL: Duration = Duration::from_secs(5);

#[derive(Clone, Copy)]
struct Lang(bool);

impl Lang {
    fn detect() -> Lang {
        Lang(
            sys_locale::get_locale()
                .map(|l| l.to_lowercase().starts_with("es"))
                .unwrap_or(false),
        )
    }
    fn title(self) -> &'static str {
        "brain · vault"
    }
    fn op(self, kind: &str, path: &str) -> String {
        match (self.0, kind) {
            (true, "create") => format!("Nuevo: {path}"),
            (true, "delete") => format!("Eliminado: {path}"),
            (true, _) => format!("Modificado: {path}"),
            (false, "create") => format!("New: {path}"),
            (false, "delete") => format!("Deleted: {path}"),
            (false, _) => format!("Changed: {path}"),
        }
    }
    fn many(self, n: u64) -> String {
        if self.0 {
            format!("{n} cambios en tu vault")
        } else {
            format!("{n} changes in your vault")
        }
    }
    pub fn test_body(self) -> &'static str {
        if self.0 {
            "Así se ven las notificaciones de brain."
        } else {
            "This is what brain's notifications look like."
        }
    }
}

pub fn test_body() -> &'static str {
    Lang::detect().test_body()
}

pub fn show(app: &AppHandle, title: &str, body: &str) {
    let r = app.notification().builder().title(title).body(body).show();
    match r {
        Ok(()) => applog::line(format!("notification: {body}")),
        Err(e) => applog::line(format!("notification failed: {e}")),
    }
}

fn kind_of(op: &str) -> &'static str {
    match op {
        "create" => "create",
        "delete" => "delete",
        _ => "update",
    }
}

/// The part of the loop that decides what to say; separate so it can be tested without a window.
pub fn describe(lang_es: bool, changes: &[Value], total: u64, prefs: &Value) -> Vec<String> {
    let lang = Lang(lang_es);
    let on = |k: &str| prefs[k].as_bool().unwrap_or(true);
    // a move leaves two records (origin and destination): the destination is enough
    let items: Vec<(&str, &str)> = changes
        .iter()
        .filter(|c| !(c["op"] == "move" && !c["was_new"].as_bool().unwrap_or(false)))
        .map(|c| {
            (
                kind_of(c["op"].as_str().unwrap_or("")),
                c["path"].as_str().unwrap_or(""),
            )
        })
        .filter(|(k, _)| on(k))
        .collect();
    if total as usize > changes.len() {
        return vec![lang.many(total)];
    }
    if items.len() > 3 {
        let tail: Vec<&str> = items.iter().rev().take(3).rev().map(|(_, p)| *p).collect();
        return vec![format!(
            "{}\n{}",
            lang.many(items.len() as u64),
            tail.join("\n")
        )];
    }
    items.iter().map(|(k, p)| lang.op(k, p)).collect()
}

fn flash_tray(app: &AppHandle) {
    let Some(tray) = app.tray_by_id("main") else {
        return;
    };
    #[cfg(target_os = "macos")]
    let _ = tray.set_title(Some("●"));
    let _ = tray.set_tooltip(Some("brain ●"));
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(8));
        if let Some(tray) = app.tray_by_id("main") {
            #[cfg(target_os = "macos")]
            let _ = tray.set_title(None::<&str>);
            let _ = tray.set_tooltip(Some("brain"));
        }
    });
}

pub fn spawn(app: AppHandle) {
    std::thread::spawn(move || {
        let state = app.state::<AppState>();
        let lang = Lang::detect();
        let mut last: Option<i64> = None;
        let mut was_up = true;
        loop {
            std::thread::sleep(POLL);
            let be = &state.backend;
            let cfg = match be.get_json("/api/app") {
                Ok(c) => c,
                Err(_) => {
                    if was_up && state.ready.load(Ordering::SeqCst) {
                        applog::line("the dashboard does not answer; waiting for it");
                    }
                    was_up = false;
                    last = None; // when it is back, only what happens from then on is announced
                    continue;
                }
            };
            was_up = true;
            state.close_to_tray.store(
                cfg["close_to_tray"].as_bool().unwrap_or(true),
                Ordering::SeqCst,
            );
            let after = last.unwrap_or(-1);
            let Ok(r) = be.get_json(&format!("/api/activity?after={after}")) else {
                continue;
            };
            if last.is_none() {
                last = r["last_id"].as_i64(); // on start: no news about what was already there
                continue;
            }
            let changes: Vec<Value> = r["changes"].as_array().cloned().unwrap_or_default();
            let total = r["total"].as_u64().unwrap_or(0);
            last = if total as usize > changes.len() {
                r["max_id"].as_i64()
            } else {
                r["last_id"].as_i64()
            };
            if changes.is_empty() {
                continue;
            }
            let lines = describe(lang.0, &changes, total, &cfg["notify"]);
            if lines.is_empty() || !cfg["notify"]["on"].as_bool().unwrap_or(true) {
                continue;
            }
            applog::line(format!(
                "{} change(s) in the vault",
                total.max(changes.len() as u64)
            ));
            for body in &lines {
                show(&app, lang.title(), body);
            }
            flash_tray(&app);
            state.remember(&lines);
            if cfg["notch"].as_bool().unwrap_or(true) {
                notch::show(&app);
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn ch(id: i64, op: &str, path: &str, was_new: bool) -> Value {
        json!({"id": id, "op": op, "path": path, "was_new": was_new})
    }

    #[test]
    fn one_line_per_change_up_to_three() {
        let c = vec![
            ch(1, "create", "a.md", true),
            ch(2, "delete", "b.md", false),
        ];
        let out = describe(false, &c, 2, &json!({}));
        assert_eq!(out, vec!["New: a.md", "Deleted: b.md"]);
    }

    #[test]
    fn more_than_three_are_grouped_with_the_last_three() {
        let c: Vec<Value> = (1..=5)
            .map(|i| ch(i, "update", &format!("n{i}.md"), false))
            .collect();
        let out = describe(true, &c, 5, &json!({}));
        assert_eq!(out, vec!["5 cambios en tu vault\nn3.md\nn4.md\nn5.md"]);
    }

    #[test]
    fn moves_count_once_and_prefs_filter_kinds() {
        let c = vec![
            ch(1, "move", "old.md", false),
            ch(2, "move", "new.md", true),
            ch(3, "delete", "x.md", false),
        ];
        let out = describe(false, &c, 3, &json!({"delete": false}));
        assert_eq!(out, vec!["Changed: new.md"]);
    }

    #[test]
    fn a_flood_is_one_notice() {
        let c: Vec<Value> = (1..=50).map(|i| ch(i, "update", "n.md", false)).collect();
        assert_eq!(
            describe(false, &c, 400, &json!({})),
            vec!["400 changes in your vault"]
        );
    }
}
