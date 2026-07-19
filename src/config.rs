use crate::atomic;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io;
use std::path::Path;

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
pub struct Config {
    pub enabled: bool,
    pub theme: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            enabled: true,
            theme: "default".into(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LoadedConfig {
    pub config: Config,
    pub warnings: Vec<String>,
}

pub fn load_config(config_dir: &Path) -> LoadedConfig {
    let path = config_dir.join("config.toml");
    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return LoadedConfig {
                config: Config::default(),
                warnings: Vec::new(),
            };
        }
        Err(error) => {
            return LoadedConfig {
                config: Config::default(),
                warnings: vec![format!("could not read {}: {error}", path.display())],
            };
        }
    };
    let table = match text.parse::<toml::Table>() {
        Ok(table) => table,
        Err(error) => {
            return LoadedConfig {
                config: Config::default(),
                warnings: vec![format!("invalid {}: {error}", path.display())],
            };
        }
    };
    let mut config = Config::default();
    let mut warnings = Vec::new();
    if let Some(value) = table.get("enabled") {
        if let Some(enabled) = value.as_bool() {
            config.enabled = enabled;
        } else {
            warnings.push("config `enabled` must be a boolean; using true".into());
        }
    }
    if let Some(value) = table.get("theme") {
        if let Some(theme) = value.as_str().filter(|theme| !theme.trim().is_empty()) {
            config.theme = theme.to_owned();
        } else {
            warnings.push("config `theme` must be a nonempty string; using default".into());
        }
    }
    LoadedConfig { config, warnings }
}

pub fn save_config(config_dir: &Path, config: &Config) -> io::Result<()> {
    fs::create_dir_all(config_dir)?;
    let path = config_dir.join("config.toml");
    let temporary = config_dir.join(".config.toml.tmp");
    let mut table = fs::read_to_string(&path)
        .ok()
        .and_then(|text| text.parse::<toml::Table>().ok())
        .unwrap_or_default();
    table.insert("enabled".into(), toml::Value::Boolean(config.enabled));
    table.insert("theme".into(), toml::Value::String(config.theme.clone()));
    let text = toml::to_string(&table).map_err(io::Error::other)?;
    fs::write(&temporary, text)?;
    atomic::replace(&temporary, &path)
}
