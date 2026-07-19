use serde::Deserialize;
use serde_json::{Map, Value};
use std::fs;
use std::path::{Component, Path, PathBuf};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Sound {
    pub id: String,
    pub display_name: String,
    pub path: PathBuf,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Theme {
    pub id: String,
    pub name: String,
    pub description: Option<String>,
    pub author: Option<String>,
    pub directory: PathBuf,
    pub sounds: Vec<Sound>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LoadedTheme {
    pub theme: Theme,
    pub warnings: Vec<String>,
    pub fell_back: bool,
}

#[derive(Deserialize)]
struct Manifest {
    schema_version: u32,
    name: String,
    #[serde(default)]
    description: Option<String>,
    #[serde(default)]
    author: Option<String>,
    sounds: Map<String, Value>,
}

pub fn load_theme(
    plugin_root: &Path,
    config_dir: &Path,
    requested: &str,
) -> Result<LoadedTheme, String> {
    if requested == "default" {
        return load_default(plugin_root).map(|theme| LoadedTheme {
            theme,
            warnings: Vec::new(),
            fell_back: false,
        });
    }

    let personal = safe_theme_id(requested)
        .then(|| config_dir.join("themes").join(requested))
        .ok_or_else(|| "theme id contains path traversal".to_owned())
        .and_then(|directory| load_manifest(requested, directory));
    match personal {
        Ok(theme) => Ok(LoadedTheme {
            theme,
            warnings: Vec::new(),
            fell_back: false,
        }),
        Err(error) => Ok(LoadedTheme {
            theme: load_default(plugin_root)?,
            warnings: vec![format!(
                "invalid personal theme `{requested}` ({error}); using bundled default"
            )],
            fell_back: true,
        }),
    }
}

fn load_default(plugin_root: &Path) -> Result<Theme, String> {
    load_manifest(
        "default",
        plugin_root.join("sounds").join("themes").join("default"),
    )
    .map_err(|error| format!("bundled default theme is invalid: {error}"))
}

fn load_manifest(id: &str, directory: PathBuf) -> Result<Theme, String> {
    let path = directory.join("theme.json");
    let text = fs::read_to_string(&path).map_err(|error| format!("{}: {error}", path.display()))?;
    let manifest: Manifest =
        serde_json::from_str(&text).map_err(|error| format!("{}: {error}", path.display()))?;
    if manifest.schema_version != 1 {
        return Err(format!(
            "unsupported schema_version {}",
            manifest.schema_version
        ));
    }
    if manifest.name.trim().is_empty() {
        return Err("theme name is empty".into());
    }
    if manifest.sounds.is_empty() {
        return Err("sounds must be nonempty".into());
    }
    let canonical_directory = directory
        .canonicalize()
        .map_err(|error| format!("{}: {error}", directory.display()))?;
    let mut sounds = Vec::with_capacity(manifest.sounds.len());
    for (sound_id, display_name) in manifest.sounds {
        if !safe_sound_id(&sound_id) {
            return Err(format!("sound id `{sound_id}` contains path traversal"));
        }
        let display_name = display_name
            .as_str()
            .filter(|name| !name.trim().is_empty())
            .ok_or_else(|| format!("display name for `{sound_id}` must be nonempty"))?;
        let sound_path = directory.join(format!("{sound_id}.wav"));
        let canonical_sound = sound_path
            .canonicalize()
            .map_err(|error| format!("{}: {error}", sound_path.display()))?;
        if !canonical_sound.starts_with(&canonical_directory) || !canonical_sound.is_file() {
            return Err(format!("sound `{sound_id}` resolves outside its theme"));
        }
        sounds.push(Sound {
            id: sound_id,
            display_name: display_name.to_owned(),
            path: sound_path,
        });
    }
    Ok(Theme {
        id: id.to_owned(),
        name: manifest.name,
        description: manifest.description,
        author: manifest.author,
        directory,
        sounds,
    })
}

fn safe_theme_id(id: &str) -> bool {
    !id.is_empty() && safe_component(id)
}

fn safe_sound_id(id: &str) -> bool {
    !id.is_empty() && safe_component(id) && !id.ends_with(".wav")
}

fn safe_component(value: &str) -> bool {
    if value.contains(['/', '\\']) || value.contains(':') {
        return false;
    }
    let mut components = Path::new(value).components();
    matches!(components.next(), Some(Component::Normal(_))) && components.next().is_none()
}
