use std::ffi::OsStr;
#[cfg(any(target_os = "macos", target_os = "linux", test))]
use std::io;
use std::path::Path;
#[cfg(any(target_os = "macos", target_os = "linux"))]
use std::process::{Command, Stdio};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Platform {
    Macos,
    Linux,
    Windows,
    Other,
}

impl Platform {
    pub const fn current() -> Self {
        if cfg!(target_os = "macos") {
            Self::Macos
        } else if cfg!(target_os = "linux") {
            Self::Linux
        } else if cfg!(windows) {
            Self::Windows
        } else {
            Self::Other
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AudioBackend {
    Command(&'static str),
    WinMm,
}

pub fn select_backend(
    platform: Platform,
    mut available: impl FnMut(&str) -> bool,
) -> Option<AudioBackend> {
    match platform {
        Platform::Macos => Some(AudioBackend::Command("afplay")),
        Platform::Linux => ["pw-play", "paplay", "aplay"]
            .into_iter()
            .find(|command| available(command))
            .map(AudioBackend::Command),
        Platform::Windows => Some(AudioBackend::WinMm),
        Platform::Other => None,
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Playback {
    Started,
    Unavailable,
    Failed(String),
}

pub fn play(path: &Path) -> Playback {
    #[cfg(target_os = "macos")]
    {
        return spawn_player("afplay", path);
    }
    #[cfg(target_os = "linux")]
    {
        return play_command_candidates(&["pw-play", "paplay", "aplay"], path, spawn_player_result);
    }
    #[cfg(windows)]
    {
        return play_winmm(path);
    }
    #[allow(unreachable_code)]
    Playback::Unavailable
}

#[cfg(target_os = "macos")]
fn spawn_player(command: &str, path: &Path) -> Playback {
    match spawn_player_result(command, path) {
        Ok(()) => Playback::Started,
        Err(error) if error.kind() == io::ErrorKind::NotFound => Playback::Unavailable,
        Err(error) => Playback::Failed(error.to_string()),
    }
}

#[cfg(any(target_os = "macos", target_os = "linux"))]
fn spawn_player_result(command: &str, path: &Path) -> io::Result<()> {
    match Command::new(command)
        .arg(path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(_) => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(any(target_os = "linux", test))]
fn play_command_candidates(
    commands: &[&str],
    path: &Path,
    mut spawn: impl FnMut(&str, &Path) -> io::Result<()>,
) -> Playback {
    for command in commands {
        match spawn(command, path) {
            Ok(()) => return Playback::Started,
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::NotFound | io::ErrorKind::PermissionDenied
                ) => {}
            Err(error) => return Playback::Failed(error.to_string()),
        }
    }
    Playback::Unavailable
}

pub fn command_available(command: &str) -> bool {
    std::env::var_os("PATH")
        .is_some_and(|path| command_available_on_path(command, path.as_os_str()))
}

fn command_available_on_path(command: &str, path: &OsStr) -> bool {
    std::env::split_paths(path).any(|directory| {
        let candidate = directory.join(command);
        executable_file(&candidate)
            || cfg!(windows)
                && ["exe", "com", "bat"]
                    .iter()
                    .any(|extension| executable_file(&candidate.with_extension(extension)))
    })
}

fn executable_file(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        std::fs::metadata(path).is_ok_and(|metadata| metadata.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    true
}

#[cfg(windows)]
fn play_winmm(path: &Path) -> Playback {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Media::Audio::{PlaySoundW, SND_FILENAME, SND_NODEFAULT};

    let path: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let played = unsafe {
        PlaySoundW(
            path.as_ptr(),
            std::ptr::null_mut(),
            SND_FILENAME | SND_NODEFAULT,
        )
    };
    if played != 0 {
        Playback::Started
    } else {
        Playback::Failed(std::io::Error::last_os_error().to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io;

    #[test]
    fn permission_denied_candidate_falls_through_to_the_next_player() {
        let mut attempts = Vec::new();
        let playback =
            play_command_candidates(&["first", "second"], Path::new("tone.wav"), |command, _| {
                attempts.push(command.to_owned());
                if command == "first" {
                    Err(io::Error::from(io::ErrorKind::PermissionDenied))
                } else {
                    Ok(())
                }
            });

        assert_eq!(playback, Playback::Started);
        assert_eq!(attempts, vec!["first", "second"]);
    }

    #[cfg(unix)]
    #[test]
    fn readiness_rejects_non_executable_files_on_path() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let command = dir.path().join("player");
        std::fs::write(&command, "not executable").unwrap();
        let mut permissions = std::fs::metadata(&command).unwrap().permissions();
        permissions.set_mode(0o644);
        std::fs::set_permissions(&command, permissions).unwrap();

        assert!(!command_available_on_path("player", dir.path().as_os_str()));
        let mut permissions = std::fs::metadata(&command).unwrap().permissions();
        permissions.set_mode(0o755);
        std::fs::set_permissions(&command, permissions).unwrap();
        assert!(command_available_on_path("player", dir.path().as_os_str()));
    }
}
