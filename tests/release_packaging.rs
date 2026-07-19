#![cfg(unix)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

const VERSION: &str = env!("CARGO_PKG_VERSION");
const UNIX_TARGETS: [&str; 4] = [
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-gnu",
];
const WINDOWS_TARGET: &str = "x86_64-pc-windows-msvc";

fn script() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts/package-release.sh")
}

fn fixture_inputs(root: &Path) -> PathBuf {
    let inputs = root.join("inputs");
    for target in UNIX_TARGETS {
        let directory = inputs.join(format!("binary-{target}"));
        fs::create_dir_all(&directory).unwrap();
        fs::write(directory.join("session-sounds"), format!("binary:{target}")).unwrap();
    }
    let directory = inputs.join(format!("binary-{WINDOWS_TARGET}"));
    fs::create_dir_all(&directory).unwrap();
    fs::write(
        directory.join("session-sounds.exe"),
        format!("binary:{WINDOWS_TARGET}"),
    )
    .unwrap();
    inputs
}

fn run(version: &str, inputs: &Path, output: &Path) -> Output {
    Command::new("sh")
        .arg(script())
        .arg(version)
        .arg(inputs)
        .arg(output)
        .output()
        .unwrap()
}

fn command_lines(program: &str, arguments: &[&str], path: &Path) -> Vec<String> {
    let output = Command::new(program)
        .args(arguments)
        .arg(path)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(str::to_owned)
        .collect()
}

#[test]
fn packages_five_exact_archives_with_license_and_valid_checksums() {
    let temporary = tempfile::tempdir().unwrap();
    let inputs = fixture_inputs(temporary.path());
    let output = temporary.path().join("release");

    let result = run(VERSION, &inputs, &output);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );

    let mut names: Vec<_> = fs::read_dir(&output)
        .unwrap()
        .map(|entry| entry.unwrap().file_name().into_string().unwrap())
        .collect();
    names.sort();
    let mut expected = vec!["SHA256SUMS".to_owned()];
    expected
        .extend(UNIX_TARGETS.map(|target| format!("session-sounds-v{VERSION}-{target}.tar.gz")));
    expected.push(format!("session-sounds-v{VERSION}-{WINDOWS_TARGET}.zip"));
    expected.sort();
    assert_eq!(names, expected);

    for target in UNIX_TARGETS {
        let archive = output.join(format!("session-sounds-v{VERSION}-{target}.tar.gz"));
        let mut members = command_lines("tar", &["-tzf"], &archive);
        members.sort();
        assert_eq!(members, ["LICENSE", "session-sounds"]);
    }
    let archive = output.join(format!("session-sounds-v{VERSION}-{WINDOWS_TARGET}.zip"));
    let mut members = command_lines("unzip", &["-Z1"], &archive);
    members.sort();
    assert_eq!(members, ["LICENSE", "session-sounds.exe"]);

    let checksum_status = Command::new("sh")
        .arg("-c")
        .arg("if command -v sha256sum >/dev/null 2>&1; then sha256sum --check SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi")
        .current_dir(&output)
        .status()
        .unwrap();
    assert!(checksum_status.success());
    let checksums = fs::read_to_string(output.join("SHA256SUMS")).unwrap();
    assert_eq!(checksums.lines().count(), 5);
    assert!(!checksums.contains('/'));
}

#[test]
fn packages_with_workflow_relative_paths() {
    let temporary = tempfile::tempdir().unwrap();
    fixture_inputs(temporary.path());

    let result = Command::new("sh")
        .arg(script())
        .arg(VERSION)
        .arg("inputs")
        .arg("release")
        .current_dir(temporary.path())
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(temporary.path().join("release/SHA256SUMS").is_file());
}

#[test]
fn rejects_invalid_version_missing_input_and_preexisting_output() {
    let temporary = tempfile::tempdir().unwrap();
    let inputs = fixture_inputs(temporary.path());
    let invalid_output = temporary.path().join("invalid-release");
    let invalid = run("v1.0.0", &inputs, &invalid_output);
    assert!(!invalid.status.success());
    assert!(!invalid_output.exists());

    fs::remove_file(
        inputs
            .join("binary-aarch64-apple-darwin")
            .join("session-sounds"),
    )
    .unwrap();
    let missing_output = temporary.path().join("missing-release");
    let missing = run(VERSION, &inputs, &missing_output);
    assert!(!missing.status.success());
    assert!(!missing_output.exists());

    let existing_output = temporary.path().join("existing-release");
    fs::create_dir(&existing_output).unwrap();
    fs::write(existing_output.join("sentinel"), b"keep").unwrap();
    let existing = run(VERSION, &inputs, &existing_output);
    assert!(!existing.status.success());
    assert_eq!(fs::read(existing_output.join("sentinel")).unwrap(), b"keep");
}
