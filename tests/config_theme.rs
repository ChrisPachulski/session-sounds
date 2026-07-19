use session_sounds::config::{load_config, save_config, toggle_config, Config};
use session_sounds::theme::load_theme;
use std::fs;
use std::path::Path;
use std::sync::{Arc, Barrier};
use std::thread;
use tempfile::tempdir;

fn write_wav(path: &Path) {
    fs::write(path, b"RIFFtestWAVE").unwrap();
}

#[test]
fn config_defaults_when_missing() {
    let dir = tempdir().unwrap();
    let loaded = load_config(dir.path());

    assert_eq!(loaded.config, Config::default());
    assert!(loaded.warnings.is_empty());
}

#[test]
fn config_tolerates_unknown_keys_and_falls_back_per_invalid_value() {
    let dir = tempdir().unwrap();
    fs::write(
        dir.path().join("config.toml"),
        "enabled = \"yes\"\ntheme = 42\nfuture_option = true\n",
    )
    .unwrap();

    let loaded = load_config(dir.path());

    assert_eq!(loaded.config, Config::default());
    assert_eq!(loaded.warnings.len(), 2);
}

#[test]
fn config_round_trip_persists_enabled_and_theme() {
    let dir = tempdir().unwrap();
    let expected = Config {
        enabled: false,
        theme: "quiet".into(),
    };

    save_config(dir.path(), &expected).unwrap();

    assert_eq!(load_config(dir.path()).config, expected);
}

#[test]
fn saving_public_config_preserves_unknown_future_keys() {
    let dir = tempdir().unwrap();
    fs::write(
        dir.path().join("config.toml"),
        "enabled = true\ntheme = \"default\"\nfuture_option = \"keep-me\"\n",
    )
    .unwrap();

    save_config(
        dir.path(),
        &Config {
            enabled: false,
            theme: "default".into(),
        },
    )
    .unwrap();

    let saved = fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert!(saved.contains("future_option = \"keep-me\""));
    assert!(saved.contains("enabled = false"));
}

#[test]
fn simultaneous_toggles_are_serialized_without_losing_an_update() {
    let dir = tempdir().unwrap();
    let path = Arc::new(dir.path().to_path_buf());
    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for _ in 0..2 {
        let path = Arc::clone(&path);
        let barrier = Arc::clone(&barrier);
        workers.push(thread::spawn(move || {
            barrier.wait();
            toggle_config(&path).unwrap().config.enabled
        }));
    }
    barrier.wait();
    let mut outcomes: Vec<_> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect();
    outcomes.sort_unstable();

    assert_eq!(outcomes, vec![false, true]);
    assert!(load_config(&path).config.enabled);
}

#[cfg(unix)]
#[test]
fn stale_fixed_temp_symlink_is_never_followed_or_replaced() {
    use std::os::unix::fs::symlink;

    let dir = tempdir().unwrap();
    let sentinel_dir = tempdir().unwrap();
    let sentinel = sentinel_dir.path().join("sentinel");
    fs::write(&sentinel, "untouched").unwrap();
    let stale_temp = dir.path().join(".config.toml.tmp");
    symlink(&sentinel, &stale_temp).unwrap();

    save_config(
        dir.path(),
        &Config {
            enabled: false,
            theme: "default".into(),
        },
    )
    .unwrap();

    assert_eq!(fs::read_to_string(&sentinel).unwrap(), "untouched");
    assert!(stale_temp.is_symlink());
    assert!(!load_config(dir.path()).config.enabled);
}

#[test]
fn bundled_default_retains_the_seven_contract_sounds() {
    let config = tempdir().unwrap();
    let loaded = load_theme(
        Path::new(env!("CARGO_MANIFEST_DIR")),
        config.path(),
        "default",
    )
    .unwrap();

    let actual: Vec<_> = loaded
        .theme
        .sounds
        .iter()
        .map(|sound| (sound.id.as_str(), sound.display_name.as_str()))
        .collect();
    assert_eq!(
        actual,
        vec![
            ("bright_cascade", "Bright Cascade"),
            ("warm_bell", "Warm Bell"),
            ("pulse_bounce", "Pulse Bounce"),
            ("glass_chime", "Glass Chime"),
            ("synth_stab", "Synth Stab"),
            ("kalimba", "Kalimba"),
            ("orbit", "Orbit"),
        ]
    );
    assert!(loaded.warnings.is_empty());
}

#[test]
fn valid_personal_theme_resolves_wavs_inside_its_directory() {
    let config = tempdir().unwrap();
    let theme_dir = config.path().join("themes/personal");
    fs::create_dir_all(&theme_dir).unwrap();
    fs::write(
        theme_dir.join("theme.json"),
        r#"{"schema_version":1,"name":"Mine","sounds":{"ping":"Ping"}}"#,
    )
    .unwrap();
    write_wav(&theme_dir.join("ping.wav"));

    let loaded = load_theme(
        Path::new(env!("CARGO_MANIFEST_DIR")),
        config.path(),
        "personal",
    )
    .unwrap();

    assert_eq!(loaded.theme.name, "Mine");
    assert_eq!(
        loaded.theme.sounds[0].path,
        theme_dir.join("ping.wav").canonicalize().unwrap()
    );
    assert!(!loaded.fell_back);
}

#[cfg(unix)]
#[test]
fn validated_sound_path_is_stored_canonically() {
    use std::os::unix::fs::symlink;

    let config = tempdir().unwrap();
    let theme_dir = config.path().join("themes/personal");
    fs::create_dir_all(&theme_dir).unwrap();
    fs::write(
        theme_dir.join("theme.json"),
        r#"{"schema_version":1,"name":"Mine","sounds":{"ping":"Ping"}}"#,
    )
    .unwrap();
    write_wav(&theme_dir.join("actual.wav"));
    symlink("actual.wav", theme_dir.join("ping.wav")).unwrap();

    let loaded = load_theme(
        Path::new(env!("CARGO_MANIFEST_DIR")),
        config.path(),
        "personal",
    )
    .unwrap();

    assert_eq!(
        loaded.theme.sounds[0].path,
        theme_dir.join("actual.wav").canonicalize().unwrap()
    );
}

#[test]
fn traversing_personal_theme_warns_and_falls_back_to_default() {
    let config = tempdir().unwrap();
    let theme_dir = config.path().join("themes/bad");
    fs::create_dir_all(&theme_dir).unwrap();
    fs::write(
        theme_dir.join("theme.json"),
        r#"{"schema_version":1,"name":"Bad","sounds":{"../escape":"Nope"}}"#,
    )
    .unwrap();

    let loaded = load_theme(Path::new(env!("CARGO_MANIFEST_DIR")), config.path(), "bad").unwrap();

    assert_eq!(loaded.theme.id, "default");
    assert!(loaded.fell_back);
    assert_eq!(loaded.warnings.len(), 1);
}

#[test]
fn malformed_or_empty_personal_theme_falls_back_to_default() {
    let config = tempdir().unwrap();
    let theme_dir = config.path().join("themes/empty");
    fs::create_dir_all(&theme_dir).unwrap();
    fs::write(
        theme_dir.join("theme.json"),
        r#"{"schema_version":1,"name":"Empty","sounds":{}}"#,
    )
    .unwrap();

    let loaded = load_theme(
        Path::new(env!("CARGO_MANIFEST_DIR")),
        config.path(),
        "empty",
    )
    .unwrap();

    assert_eq!(loaded.theme.id, "default");
    assert!(loaded.fell_back);
}
