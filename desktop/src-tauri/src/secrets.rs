//! OS keychain for AI keys. Never expose raw secrets to the webview.

use keyring::Entry;

const SERVICE: &str = "com.cadence.studio";
const LEGACY_SERVICE: &str = "com.ellua.studio";
pub const AI_GATEWAY: &str = "ai-gateway";
pub const ELEVENLABS: &str = "elevenlabs";
pub const FISH_AUDIO: &str = "fish-audio";
pub const CARTESIA: &str = "cartesia";

fn validate_name(name: &str) -> Result<(), String> {
    if matches!(name, AI_GATEWAY | ELEVENLABS | FISH_AUDIO | CARTESIA) {
        Ok(())
    } else {
        Err(format!("unknown secret: {name}"))
    }
}

fn entry(name: &str) -> Result<Entry, String> {
    validate_name(name)?;
    Entry::new(SERVICE, name).map_err(|e| e.to_string())
}

fn legacy_entry(name: &str) -> Result<Entry, String> {
    validate_name(name)?;
    Entry::new(LEGACY_SERVICE, name).map_err(|e| e.to_string())
}

/// Store or delete a named secret. Empty `value` removes the credential.
#[tauri::command]
pub fn set_secret(name: String, value: String) -> Result<(), String> {
    let item = entry(&name)?;
    if value.is_empty() {
        match item.delete_credential() {
            Ok(()) => Ok(()),
            Err(keyring::Error::NoEntry) => Ok(()),
            Err(err) => Err(err.to_string()),
        }
    } else {
        item.set_password(&value).map_err(|e| e.to_string())
    }
}

/// Whether a secret exists. Never returns the raw key.
#[tauri::command]
pub fn secret_is_set(name: String) -> Result<bool, String> {
    let item = entry(&name)?;
    match item.get_password() {
        Ok(password) if !password.is_empty() => Ok(true),
        _ => {
            if let Ok(legacy) = legacy_entry(&name) {
                match legacy.get_password() {
                    Ok(pwd) => Ok(!pwd.is_empty()),
                    _ => Ok(false),
                }
            } else {
                Ok(false)
            }
        }
    }
}

fn require_named_key(name: &str, missing: &str) -> Result<String, String> {
    let item = entry(name)?;
    match item.get_password() {
        Ok(password) if !password.is_empty() => Ok(password),
        _ => {
            if let Ok(legacy) = legacy_entry(name) {
                if let Ok(pwd) = legacy.get_password() {
                    if !pwd.is_empty() {
                        return Ok(pwd);
                    }
                }
            }
            Err(missing.into())
        }
    }
}

/// Internal: load the AI Gateway bearer token. Not a Tauri command.
pub fn require_ai_gateway_key() -> Result<String, String> {
    require_named_key(AI_GATEWAY, "AI Gateway key not set")
}

/// Internal: load the ElevenLabs API key. Not a Tauri command.
pub fn require_elevenlabs_key() -> Result<String, String> {
    require_named_key(ELEVENLABS, "ElevenLabs key not set")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_unknown_secret_names() {
        let err = set_secret("openai".into(), "nope".into()).unwrap_err();
        assert!(
            err.to_lowercase().contains("unknown"),
            "expected unknown secret, got {err}"
        );
        let err = secret_is_set("openai".into()).unwrap_err();
        assert!(
            err.to_lowercase().contains("unknown"),
            "expected unknown secret, got {err}"
        );
    }

    #[test]
    fn elevenlabs_is_an_allowed_secret_name() {
        let result = secret_is_set("elevenlabs".into());
        assert!(
            result.is_ok(),
            "elevenlabs should be an allowed secret name, got {result:?}"
        );
    }
}
