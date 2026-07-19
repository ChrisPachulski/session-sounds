use crate::atomic;
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

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
    load_config_unlocked(config_dir)
}

fn load_config_unlocked(config_dir: &Path) -> LoadedConfig {
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
    ConfigGuard::acquire(config_dir)?.save(config)
}

pub fn toggle_config(config_dir: &Path) -> io::Result<LoadedConfig> {
    let guard = ConfigGuard::acquire(config_dir)?;
    let mut loaded = guard.load();
    loaded.config.enabled = !loaded.config.enabled;
    guard.save(&loaded.config)?;
    Ok(loaded)
}

pub struct ConfigGuard {
    directory: PathBuf,
    lock: File,
}

impl ConfigGuard {
    pub fn acquire(config_dir: &Path) -> io::Result<Self> {
        fs::create_dir_all(config_dir)?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(config_dir.join("config.lock"))?;
        FileExt::lock_exclusive(&lock)?;
        Ok(Self {
            directory: config_dir.into(),
            lock,
        })
    }

    pub fn load(&self) -> LoadedConfig {
        load_config_unlocked(&self.directory)
    }

    pub fn save(&self, config: &Config) -> io::Result<()> {
        save_config_unlocked(&self.directory, config)
    }
}

impl Drop for ConfigGuard {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.lock);
    }
}

fn save_config_unlocked(config_dir: &Path, config: &Config) -> io::Result<()> {
    fs::create_dir_all(config_dir)?;
    let path = config_dir.join("config.toml");
    let mut table = fs::read_to_string(&path)
        .ok()
        .and_then(|text| text.parse::<toml::Table>().ok())
        .unwrap_or_default();
    table.insert("enabled".into(), toml::Value::Boolean(config.enabled));
    table.insert("theme".into(), toml::Value::String(config.theme.clone()));
    let text = toml::to_string(&table).map_err(io::Error::other)?;
    let (temporary, mut file) = create_temporary(config_dir)?;
    if let Err(error) = (|| {
        file.write_all(text.as_bytes())?;
        file.sync_all()?;
        atomic::replace(&temporary, &path)
    })() {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    Ok(())
}

fn create_temporary(config_dir: &Path) -> io::Result<(PathBuf, File)> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    loop {
        let counter = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = config_dir.join(format!(
            ".config.{}.{now}.{counter}.tmp",
            std::process::id()
        ));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
}
