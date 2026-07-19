use serde_json::Value;

#[derive(Clone, Debug)]
pub struct PluginEvent {
    pub kind: String,
    data: Value,
}

impl PluginEvent {
    pub fn parse(explicit_kind: Option<&str>, json: &str) -> Result<Self, String> {
        let root: Value = serde_json::from_str(json).map_err(|error| error.to_string())?;
        if !root.is_object() {
            return Err("event payload must be a JSON object".into());
        }
        let data = root
            .get("data")
            .filter(|value| value.is_object())
            .cloned()
            .unwrap_or_else(|| root.clone());
        let kind = explicit_kind
            .or_else(|| root.get("event").and_then(Value::as_str))
            .or_else(|| data.get("type").and_then(Value::as_str))
            .ok_or_else(|| "event kind is missing".to_owned())?;
        Ok(Self {
            kind: normalize_kind(kind),
            data,
        })
    }

    pub fn string(&self, field: &str) -> Option<String> {
        self.data
            .get(field)
            .and_then(Value::as_str)
            .or_else(|| {
                self.data
                    .get("pane")
                    .and_then(|pane| pane.get(field))
                    .and_then(Value::as_str)
            })
            .map(str::to_owned)
    }
}

fn normalize_kind(kind: &str) -> String {
    kind.replace('.', "_")
}
