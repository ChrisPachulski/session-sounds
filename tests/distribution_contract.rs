use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn read(path: impl AsRef<Path>) -> String {
    let path = path.as_ref();
    fs::read_to_string(path).unwrap_or_else(|error| panic!("{}: {error}", path.display()))
}

fn strings(value: &toml::Value) -> Vec<String> {
    value
        .as_array()
        .expect("array")
        .iter()
        .map(|entry| entry.as_str().expect("string").to_owned())
        .collect()
}

fn cargo_version() -> String {
    let cargo: toml::Value = read(root().join("Cargo.toml")).parse().expect("Cargo.toml");
    cargo["package"]["version"]
        .as_str()
        .expect("package.version")
        .to_owned()
}

#[test]
fn manifest_exposes_the_herdr_0_7_4_contract() {
    let manifest: toml::Value = read(root().join("herdr-plugin.toml"))
        .parse()
        .expect("herdr-plugin.toml");

    assert_eq!(
        manifest["id"].as_str(),
        Some("chrispachulski.session-sounds")
    );
    assert_eq!(manifest["name"].as_str(), Some("Session Sounds"));
    assert_eq!(manifest["version"].as_str(), Some("1.0.0"));
    assert_eq!(manifest["min_herdr_version"].as_str(), Some("0.7.4"));
    assert_eq!(strings(&manifest["platforms"]), ["linux", "macos"]);

    let builds = manifest["build"].as_array().expect("build entries");
    assert_eq!(builds.len(), 2);
    assert_eq!(strings(&builds[0]["platforms"]), ["linux", "macos"]);
    assert_eq!(
        strings(&builds[0]["command"]),
        ["sh", "scripts/install-unix.sh"]
    );
    assert_eq!(strings(&builds[1]["platforms"]), ["windows"]);
    assert_eq!(
        strings(&builds[1]["command"]),
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/install-windows.ps1",
        ]
    );

    let events = manifest["events"].as_array().expect("event entries");
    let event_names: Vec<_> = events
        .iter()
        .map(|event| event["on"].as_str().expect("event name"))
        .collect();
    assert_eq!(
        event_names,
        [
            "pane.agent_detected",
            "pane.agent_status_changed",
            "pane.moved",
            "pane.exited",
            "pane.closed",
        ]
    );
    for event in events {
        assert_eq!(strings(&event["command"]), ["bin/session-sounds", "event"]);
    }

    let actions = manifest["actions"].as_array().expect("action entries");
    let action_ids: Vec<_> = actions
        .iter()
        .map(|action| action["id"].as_str().expect("action id"))
        .collect();
    assert_eq!(
        action_ids,
        ["toggle-mute", "reshuffle", "test-sound", "doctor"]
    );
    assert_eq!(strings(&actions[0]["contexts"]), ["global"]);
    assert_eq!(strings(&actions[1]["contexts"]), ["pane"]);
    assert_eq!(strings(&actions[2]["contexts"]), ["pane"]);
    assert_eq!(strings(&actions[3]["contexts"]), ["global", "workspace"]);
    for action in actions {
        assert_eq!(
            strings(&action["command"]),
            [
                "bin/session-sounds".to_owned(),
                action["id"].as_str().expect("action id").to_owned(),
            ]
        );
    }
}

#[test]
fn release_identity_and_asset_names_agree_everywhere() {
    let version = cargo_version();
    let manifest: toml::Value = read(root().join("herdr-plugin.toml"))
        .parse()
        .expect("herdr-plugin.toml");
    assert_eq!(manifest["version"].as_str(), Some(version.as_str()));

    let unix_installer = read(root().join("scripts/install-unix.sh"));
    let windows_installer = read(root().join("scripts/install-windows.ps1"));
    let release = read(root().join(".github/workflows/release.yml"));
    for target in [
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "aarch64-unknown-linux-gnu",
        "x86_64-unknown-linux-gnu",
        "x86_64-pc-windows-msvc",
    ] {
        assert!(release.contains(target), "release omits {target}");
    }
    for text in [&unix_installer, &windows_installer, &release] {
        assert!(text.contains("ChrisPachulski/session-sounds"));
        assert!(text.contains("SHA256SUMS"));
        assert!(!text.contains("vv1.0.0"));
    }
    assert!(unix_installer.contains("session-sounds-v${VERSION}-${TARGET}.tar.gz"));
    assert!(windows_installer.contains("session-sounds-v$Version-$Target.zip"));
    assert!(release.contains("session-sounds-v${VERSION}-${TARGET}"));
}

#[test]
fn release_inputs_retain_exactly_the_default_theme_inventory() {
    let root = root();
    let theme: serde_json::Value =
        serde_json::from_str(&read(root.join("sounds/themes/default/theme.json")))
            .expect("theme.json");
    let ids: BTreeSet<_> = theme["sounds"]
        .as_object()
        .expect("sounds")
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(
        ids,
        BTreeSet::from([
            "bright_cascade",
            "glass_chime",
            "kalimba",
            "orbit",
            "pulse_bounce",
            "synth_stab",
            "warm_bell",
        ])
    );
    for id in ids {
        assert!(root
            .join(format!("sounds/themes/default/{id}.wav"))
            .is_file());
    }
    let gitignore = read(root.join(".gitignore"));
    assert!(!gitignore.lines().any(|line| {
        let line = line.trim();
        !line.starts_with('#') && line.contains("sounds") && line.contains("wav")
    }));
}

#[test]
fn installers_stage_verify_and_replace_in_that_order() {
    let unix = read(root().join("scripts/install-unix.sh"));
    let windows = read(root().join("scripts/install-windows.ps1"));

    let unix_verify = unix
        .rfind("verify_checksum \"$ARCHIVE\"")
        .expect("Unix checksum verification");
    let unix_extract = unix
        .rfind("extract_archive \"$ARCHIVE\"")
        .expect("Unix extraction");
    let unix_replace = unix
        .rfind("install_binary \"$EXTRACTED/session-sounds\"")
        .expect("Unix replacement");
    assert!(unix_verify < unix_extract && unix_extract < unix_replace);
    assert!(unix.contains("mktemp"));
    assert!(unix.contains("curl"));
    assert!(unix.contains("--retry 3"));
    assert!(unix.contains("sha256sum") && unix.contains("shasum"));

    let windows_verify = windows
        .rfind("Confirm-ArchiveChecksum -ArchivePath")
        .expect("Windows checksum verification");
    let windows_replace = windows
        .rfind("Install-Binary -ArchivePath")
        .expect("Windows replacement");
    assert!(windows_verify < windows_replace);
    assert!(windows.contains("Expand-Archive -LiteralPath $ArchivePath"));
    assert!(windows.contains("Invoke-WebRequest"));
    assert!(windows.contains("Get-FileHash"));
    assert!(windows.contains("[System.IO.File]::Replace"));
}

#[test]
fn living_documentation_has_no_legacy_runtime_contracts() {
    let documents = [
        read(root().join("README.md")),
        read(root().join(".codex/skills/session-sounds/SKILL.md")),
        read(root().join(".claude/skills/session-sounds/SKILL.md")),
    ];
    for document in documents {
        for removed in [
            "install_claude_sounds.py",
            "agent_launcher.py",
            "sound_manager.py",
            "SESSION_SOUNDS_DISABLED",
            "SESSION_SOUNDS_THEME",
            "CLAUDE_SOUND_PACK",
            "~/.claude/sounds",
            "~/.codex/sessions",
            "25 copyright-free sounds",
        ] {
            assert!(
                !document.contains(removed),
                "living documentation still promises removed contract {removed}"
            );
        }
    }
}

#[test]
fn automation_covers_three_os_ci_and_one_stable_release() {
    let ci = read(root().join(".github/workflows/ci.yml"));
    for os in ["ubuntu-latest", "macos-latest", "windows-latest"] {
        assert!(ci.contains(os), "CI omits {os}");
    }
    for command in [
        "cargo fmt --all --check",
        "cargo clippy --locked --all-targets --all-features -- -D warnings",
        "cargo test --locked --all-targets --all-features",
    ] {
        assert!(ci.contains(command), "CI omits {command}");
    }

    let release = read(root().join(".github/workflows/release.yml"));
    assert!(release.contains("^v(0|[1-9][0-9]*)"));
    assert!(release.contains("uses: taiki-e/install-action@v2"));
    assert!(release.contains("cross build --release --locked"));
    assert!(release.contains("permissions:\n      contents: write"));
    assert_eq!(
        release
            .matches("uses: softprops/action-gh-release@v2")
            .count(),
        1
    );
    assert!(release.contains("sha256sum session-sounds-v* > SHA256SUMS"));
}
