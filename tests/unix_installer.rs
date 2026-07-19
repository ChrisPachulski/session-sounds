#![cfg(unix)]

use std::fs;
use std::os::unix::fs::symlink;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use tempfile::TempDir;

const TARGET: &str = "x86_64-apple-darwin";
const VERSION: &str = env!("CARGO_PKG_VERSION");

fn write_executable(path: &Path, text: &str) {
    fs::write(path, text).unwrap();
    let mut permissions = fs::metadata(path).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(path, permissions).unwrap();
}

struct Fixture {
    _directory: TempDir,
    plugin_root: PathBuf,
    fake_bin: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let directory = tempfile::tempdir().unwrap();
        let plugin_root = directory.path().join("plugin");
        let scripts = plugin_root.join("scripts");
        let fake_bin = directory.path().join("fake-bin");
        fs::create_dir_all(&scripts).unwrap();
        fs::create_dir_all(&fake_bin).unwrap();
        fs::copy(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts/install-unix.sh"),
            scripts.join("install-unix.sh"),
        )
        .unwrap();
        fs::write(
            plugin_root.join("Cargo.toml"),
            format!("[package]\nname = \"fixture\"\nversion = \"{VERSION}\"\n"),
        )
        .unwrap();
        write_executable(
            &fake_bin.join("uname"),
            "#!/bin/sh\ncase \"$1\" in -s) printf '%s\\n' \"${FAKE_UNAME_S:-Darwin}\" ;; -m) printf '%s\\n' \"${FAKE_UNAME_M:-x86_64}\" ;; *) exit 2 ;; esac\n",
        );
        write_executable(
            &fake_bin.join("curl"),
            "#!/bin/sh\nset -eu\nif [ \"${FAKE_CURL_FAIL:-0}\" = 1 ]; then exit 22; fi\nout=\nurl=\nwhile [ \"$#\" -gt 0 ]; do\n  case \"$1\" in\n    --output) out=$2; shift 2 ;;\n    http*) url=$1; shift ;;\n    *) shift ;;\n  esac\ndone\n[ -n \"$out\" ] && [ -n \"$url\" ]\nif [ -n \"${FAKE_CURL_MARKER:-}\" ]; then : > \"$FAKE_CURL_MARKER\"; fi\ncase \"$url\" in\n  */SHA256SUMS) cp \"$FAKE_CHECKSUMS\" \"$out\" ;;\n  *) cp \"$FAKE_ARCHIVE\" \"$out\" ;;\nesac\n",
        );
        Self {
            _directory: directory,
            plugin_root,
            fake_bin,
        }
    }

    fn installer(&self) -> PathBuf {
        self.plugin_root.join("scripts/install-unix.sh")
    }

    fn path(&self) -> String {
        format!(
            "{}:{}",
            self.fake_bin.display(),
            std::env::var("PATH").unwrap_or_default()
        )
    }

    fn run(&self, archive: &Path, checksums: &Path) -> Output {
        Command::new("sh")
            .arg(self.installer())
            .env("PATH", self.path())
            .env("FAKE_ARCHIVE", archive)
            .env("FAKE_CHECKSUMS", checksums)
            .output()
            .unwrap()
    }
}

fn command_path(name: &str) -> PathBuf {
    let output = Command::new("/bin/sh")
        .args(["-c", "command -v \"$1\"", "sh", name])
        .output()
        .unwrap();
    assert!(output.status.success());
    PathBuf::from(String::from_utf8(output.stdout).unwrap().trim())
}

fn checksum(path: &Path) -> String {
    for (program, arguments) in [
        ("sha256sum", vec![path]),
        ("shasum", vec![Path::new("-a"), Path::new("256"), path]),
    ] {
        let output = Command::new(program).args(arguments).output();
        if let Ok(output) = output {
            if output.status.success() {
                return String::from_utf8(output.stdout)
                    .unwrap()
                    .split_whitespace()
                    .next()
                    .unwrap()
                    .to_owned();
            }
        }
    }
    panic!("no SHA-256 command available for installer test")
}

fn valid_archive(directory: &Path, contents: &[u8]) -> PathBuf {
    let source = directory.join("archive-source");
    fs::create_dir_all(&source).unwrap();
    fs::write(source.join("session-sounds"), contents).unwrap();
    fs::write(source.join("LICENSE"), b"license").unwrap();
    let archive = directory.join("fixture.tar.gz");
    let status = Command::new("tar")
        .args(["-czf"])
        .arg(&archive)
        .arg("-C")
        .arg(&source)
        .args(["session-sounds", "LICENSE"])
        .status()
        .unwrap();
    assert!(status.success());
    archive
}

fn archive_with_extra_member(directory: &Path) -> PathBuf {
    let source = directory.join("extra-source");
    fs::create_dir_all(&source).unwrap();
    fs::write(source.join("session-sounds"), b"malicious replacement").unwrap();
    fs::write(source.join("LICENSE"), b"license").unwrap();
    fs::write(source.join("unexpected"), b"not allowed").unwrap();
    let archive = directory.join("extra.tar.gz");
    let status = Command::new("tar")
        .args(["-czf"])
        .arg(&archive)
        .arg("-C")
        .arg(&source)
        .args(["session-sounds", "LICENSE", "unexpected"])
        .status()
        .unwrap();
    assert!(status.success());
    archive
}

fn archive_with_symlink_binary(directory: &Path) -> PathBuf {
    let source = directory.join("symlink-source");
    fs::create_dir_all(&source).unwrap();
    fs::write(source.join("payload"), b"symlink payload").unwrap();
    fs::write(source.join("LICENSE"), b"license").unwrap();
    symlink("payload", source.join("session-sounds")).unwrap();
    let archive = directory.join("symlink.tar.gz");
    let status = Command::new("tar")
        .args(["-czf"])
        .arg(&archive)
        .arg("-C")
        .arg(&source)
        .args(["session-sounds", "LICENSE"])
        .status()
        .unwrap();
    assert!(status.success());
    archive
}

fn checksums(directory: &Path, hash: &str) -> PathBuf {
    let path = directory.join("SHA256SUMS.fixture");
    fs::write(
        &path,
        format!("{hash}  session-sounds-v{VERSION}-{TARGET}.tar.gz\n{hash}  near-match.tar.gz\n"),
    )
    .unwrap();
    path
}

#[test]
fn verified_archive_installs_an_executable() {
    let fixture = Fixture::new();
    let archive = valid_archive(fixture._directory.path(), b"new binary");
    let checksums = checksums(fixture._directory.path(), &checksum(&archive));

    let output = fixture.run(&archive, &checksums);

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let binary = fixture.plugin_root.join("bin/session-sounds");
    assert_eq!(fs::read(&binary).unwrap(), b"new binary");
    assert_ne!(
        fs::metadata(binary).unwrap().permissions().mode() & 0o111,
        0
    );
}

#[test]
fn unsupported_target_fails_before_any_download() {
    let fixture = Fixture::new();
    let marker = fixture._directory.path().join("curl-called");
    let output = Command::new("sh")
        .arg(fixture.installer())
        .env("PATH", fixture.path())
        .env("FAKE_UNAME_M", "riscv64")
        .env("FAKE_CURL_MARKER", &marker)
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("unsupported platform"));
    assert!(!marker.exists());
}

#[test]
fn missing_download_tool_is_actionable() {
    let fixture = Fixture::new();
    fs::remove_file(fixture.fake_bin.join("curl")).unwrap();
    symlink(command_path("awk"), fixture.fake_bin.join("awk")).unwrap();
    symlink(command_path("dirname"), fixture.fake_bin.join("dirname")).unwrap();

    let output = Command::new("/bin/sh")
        .arg(fixture.installer())
        .env("PATH", &fixture.fake_bin)
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr)
        .contains("required tool 'curl' was not found on PATH"));
}

#[test]
fn download_failure_is_actionable_and_never_creates_a_binary() {
    let fixture = Fixture::new();
    let output = Command::new("sh")
        .arg(fixture.installer())
        .env("PATH", fixture.path())
        .env("FAKE_CURL_FAIL", "1")
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("download failed:"));
    assert!(!fixture.plugin_root.join("bin/session-sounds").exists());
}

#[test]
fn checksum_mismatch_preserves_the_existing_binary() {
    let fixture = Fixture::new();
    let archive = valid_archive(fixture._directory.path(), b"new binary");
    let checksums = checksums(fixture._directory.path(), &"0".repeat(64));
    fs::create_dir_all(fixture.plugin_root.join("bin")).unwrap();
    let binary = fixture.plugin_root.join("bin/session-sounds");
    fs::write(&binary, b"working binary").unwrap();

    let output = fixture.run(&archive, &checksums);

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("checksum mismatch"));
    assert_eq!(fs::read(binary).unwrap(), b"working binary");
}

#[test]
fn extraction_failure_preserves_the_existing_binary() {
    let fixture = Fixture::new();
    let archive = fixture._directory.path().join("invalid.tar.gz");
    fs::write(&archive, b"not an archive").unwrap();
    let checksums = checksums(fixture._directory.path(), &checksum(&archive));
    fs::create_dir_all(fixture.plugin_root.join("bin")).unwrap();
    let binary = fixture.plugin_root.join("bin/session-sounds");
    fs::write(&binary, b"working binary").unwrap();

    let output = fixture.run(&archive, &checksums);

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("could not inspect"));
    assert_eq!(fs::read(binary).unwrap(), b"working binary");
}

#[test]
fn unexpected_archive_member_is_rejected_before_extraction() {
    let fixture = Fixture::new();
    let archive = archive_with_extra_member(fixture._directory.path());
    let checksums = checksums(fixture._directory.path(), &checksum(&archive));
    fs::create_dir_all(fixture.plugin_root.join("bin")).unwrap();
    let binary = fixture.plugin_root.join("bin/session-sounds");
    fs::write(&binary, b"working binary").unwrap();

    let output = fixture.run(&archive, &checksums);

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("unsafe archive members"));
    assert_eq!(fs::read(binary).unwrap(), b"working binary");
}

#[test]
fn symlink_binary_is_rejected_and_existing_binary_is_preserved() {
    let fixture = Fixture::new();
    let archive = archive_with_symlink_binary(fixture._directory.path());
    let checksums = checksums(fixture._directory.path(), &checksum(&archive));
    fs::create_dir_all(fixture.plugin_root.join("bin")).unwrap();
    let binary = fixture.plugin_root.join("bin/session-sounds");
    fs::write(&binary, b"working binary").unwrap();

    let output = fixture.run(&archive, &checksums);

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("regular non-symlink"));
    assert_eq!(fs::read(binary).unwrap(), b"working binary");
}
