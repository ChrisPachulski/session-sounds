use std::path::Path;
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
        for command in ["pw-play", "paplay", "aplay"] {
            match spawn_player(command, path) {
                Playback::Unavailable => continue,
                result => return result,
            }
        }
        return Playback::Unavailable;
    }
    #[cfg(windows)]
    {
        return play_winmm(path);
    }
    #[allow(unreachable_code)]
    Playback::Unavailable
}

fn spawn_player(command: &str, path: &Path) -> Playback {
    match Command::new(command)
        .arg(path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(_) => Playback::Started,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Playback::Unavailable,
        Err(error) => Playback::Failed(error.to_string()),
    }
}

pub fn command_available(command: &str) -> bool {
    std::env::var_os("PATH").is_some_and(|path| {
        std::env::split_paths(&path).any(|directory| {
            let candidate = directory.join(command);
            candidate.is_file()
                || cfg!(windows)
                    && ["exe", "com", "bat"]
                        .iter()
                        .any(|extension| candidate.with_extension(extension).is_file())
        })
    })
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
