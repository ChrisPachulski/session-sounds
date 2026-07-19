use std::fs;
use std::path::{Path, PathBuf};

fn files_below(root: &Path) -> Vec<PathBuf> {
    if !root.is_dir() {
        return Vec::new();
    }
    let mut files = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory).unwrap() {
            let entry = entry.unwrap();
            let path = entry.path();
            if path
                .file_name()
                .is_some_and(|name| name == "target" || name == ".git")
            {
                continue;
            }
            if path.is_dir() {
                pending.push(path);
            } else {
                files.push(path);
            }
        }
    }
    files.sort();
    files
}

#[test]
fn pivot_has_one_rust_binary_and_no_legacy_runtime_or_python_tests() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let cargo = fs::read_to_string(root.join("Cargo.toml")).unwrap();
    assert_eq!(cargo.matches("[[bin]]").count(), 1);
    assert!(cargo.contains("name = \"session-sounds\""));
    assert!(cargo.contains("version = \"1.0.0\""));
    assert!(cargo.contains("edition = \"2021\""));

    let forbidden = root.join("install_claude_sounds.py").exists()
        || root.join("sounds/events").exists()
        || root.join("sounds/packs").exists()
        || root.join("sounds/themes/personal").exists()
        || [root.join("sounds"), root.join("tests")]
            .into_iter()
            .flat_map(|directory| files_below(&directory))
            .any(|path| path.extension().is_some_and(|extension| extension == "py"));
    assert!(!forbidden, "legacy runtime inventory remains");
}

#[test]
fn repository_retains_exactly_the_seven_bundled_wavs() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut wavs: Vec<_> = files_below(&root.join("sounds"))
        .into_iter()
        .filter(|path| path.extension().is_some_and(|extension| extension == "wav"))
        .map(|path| path.strip_prefix(root).unwrap().to_path_buf())
        .collect();
    wavs.sort();
    assert_eq!(
        wavs,
        [
            "bright_cascade.wav",
            "glass_chime.wav",
            "kalimba.wav",
            "orbit.wav",
            "pulse_bounce.wav",
            "synth_stab.wav",
            "warm_bell.wav",
        ]
        .map(|name| PathBuf::from("sounds/themes/default").join(name))
    );
}

#[test]
fn windows_audio_guard_uses_synchronous_winmm_for_short_lived_process() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let source = fs::read_to_string(root.join("src/audio.rs")).unwrap();

    assert!(source.contains("PlaySoundW"));
    assert!(!source.contains("SND_ASYNC"));
}

#[test]
fn windows_atomic_replacement_guard_uses_replace_existing_primitive() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let source = fs::read_to_string(root.join("src/atomic.rs")).unwrap();

    assert!(source.contains("MoveFileExW"));
    assert!(source.contains("MOVEFILE_REPLACE_EXISTING"));
    assert!(source.contains("MOVEFILE_WRITE_THROUGH"));
}
