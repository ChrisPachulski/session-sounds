use session_sounds::audio::{select_backend, AudioBackend, Platform};

#[test]
fn macos_uses_afplay_and_windows_uses_winmm() {
    assert_eq!(
        select_backend(Platform::Macos, |_| false),
        Some(AudioBackend::Command("afplay"))
    );
    assert_eq!(
        select_backend(Platform::Windows, |_| false),
        Some(AudioBackend::WinMm)
    );
}

#[test]
fn linux_selects_first_available_player_and_absence_is_nonfatal() {
    assert_eq!(
        select_backend(Platform::Linux, |name| name == "paplay"),
        Some(AudioBackend::Command("paplay"))
    );
    assert_eq!(select_backend(Platform::Linux, |_| false), None);
}

#[test]
fn unsupported_platform_has_no_backend() {
    assert_eq!(select_backend(Platform::Other, |_| true), None);
}
