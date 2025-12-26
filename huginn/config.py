"""Configuration management for Huginn."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CONFIG_FILE = DATA_DIR / "config.json"

# Siriuscorp query radius for bounty hunting systems (in light-years)
SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY = 50.0

# Power name -> INARA ID mapping
# URL pattern: https://inara.cz/elite/power-controlled/{id}/
POWERS = {
    "Aisling Duval": 2,
    "Archon Delaine": 10,
    "Arissa Lavigny-Duval": 4,
    "Denton Patreus": 1,
    "Edmund Mahon": 3,
    "Felicia Winters": 5,
    "Jerome Archer": 12,
    "Li Yong-Rui": 7,
    "Nakato Kaine": 13,
    "Pranav Antal": 9,
    "Yuri Grom": 11,
    "Zemina Torval": 8,
}


def get_power_url(power_name: str, page: str = "controlled") -> str | None:
    """Get INARA URL for a power.

    Args:
        power_name: Name of the power (e.g., "Jerome Archer")
        page: Type of page - "controlled", "contested", or "history"

    Returns:
        Full INARA URL or None if power not found
    """
    power_id = POWERS.get(power_name)
    if power_id is None:
        return None
    return f"https://inara.cz/elite/power-{page}/{power_id}/"


def load_config() -> dict:
    """Load user configuration from disk."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config: dict) -> None:
    """Save user configuration to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_pledged_power() -> str | None:
    """Get the user's pledged power."""
    config = load_config()
    return config.get("pledged_power")


def set_pledged_power(power_name: str) -> None:
    """Set the user's pledged power."""
    if power_name not in POWERS:
        raise ValueError(f"Unknown power: {power_name}")
    config = load_config()
    config["pledged_power"] = power_name
    save_config(config)
