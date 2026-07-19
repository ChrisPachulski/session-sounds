use session_sounds::app::{run_command, PluginEnv, SystemAudio};
use session_sounds::herdr::ProcessHerdr;

fn main() {
    let Some(command) = std::env::args().nth(1) else {
        eprintln!("usage: session-sounds <event|toggle-mute|reshuffle|test-sound|doctor>");
        std::process::exit(2);
    };
    if std::env::args().nth(2).is_some() {
        eprintln!("usage: session-sounds <event|toggle-mute|reshuffle|test-sound|doctor>");
        std::process::exit(2);
    }
    let env = PluginEnv::from_current();
    let herdr = ProcessHerdr::new(&env.herdr_bin_path);
    let audio = SystemAudio;
    let code = run_command(
        &command,
        &env,
        &herdr,
        &audio,
        &mut std::io::stdout().lock(),
        &mut std::io::stderr().lock(),
    );
    std::process::exit(code);
}
