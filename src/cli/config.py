"""
CLI Configuration
"""

from pathlib import Path
from typing import Dict, Any
import yaml


# Default CLI config
DEFAULT_CLI_CONFIG = {
    "display": {
        "color_theme": "default",
        "show_typing": True,
        "show_timestamps": True,
        "max_history": 50,
    },
    "prompt": {
        "show_seconds": False,
        "multiline": False,
    },
    "features": {
        "auto_save_history": True,
        "sound_enabled": False,
    },
}


def get_config_path() -> Path:
    """Get CLI config path"""
    home = Path.home()
    config_dir = home / ".supervisor"
    config_dir.mkdir(exist_ok=True)
    return config_dir / "cli_config.yaml"


def load_cli_config() -> Dict[str, Any]:
    """Load CLI config"""
    config_path = get_config_path()
    
    if config_path.exists():
        try:
            with open(config_path) as f:
                user_config = yaml.safe_load(f) or {}
            # Merge with defaults
            config = DEFAULT_CLI_CONFIG.copy()
            for key, value in user_config.items():
                if key in config and isinstance(value, dict):
                    config[key].update(value)
                else:
                    config[key] = value
            return config
        except Exception:
            pass
    
    return DEFAULT_CLI_CONFIG.copy()


def save_cli_config(config: Dict[str, Any]):
    """Save CLI config"""
    config_path = get_config_path()
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)