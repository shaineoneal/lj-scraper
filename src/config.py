import os
import re
import json
from pathlib import Path
from rich.console import Console

console = Console()

CONFIG_FILE = Path("config.json")

DEFAULT_SETTINGS = {
    "target": "",
    "user_data_dir": "user_profile",
    "login": False,
    "headed": False,
    "delay": None,
    "entries": True,
    "profile": True,
    "tags": True,
    "userpics": True,
    "vgifts": True,
    "memories": True,
    "photos": True
}

def load_config() -> dict:
    """Loads configuration from scraper_config.json in the current working directory.
    If the file doesn't exist, it creates a default template.
    """
    if not CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTINGS, f, indent=4)
        except Exception as e:
            console.print(f"[bold yellow]Warning: Could not create default config file: {e}[/bold yellow]")
        return DEFAULT_SETTINGS

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user_config = json.load(f)
            # Merge with defaults to ensure all keys are present
            merged = DEFAULT_SETTINGS.copy()
            merged.update(user_config)
            return merged
    except Exception as e:
        console.print(f"[bold red]Warning: Failed to parse {CONFIG_FILE}, using defaults: {e}[/bold red]")
        return DEFAULT_SETTINGS

# Loaded settings dict
settings = load_config()

USER_DATA_ENV = "USER_DATA_DIR"
DEFAULT_USER_DATA_DIR = "user_profile"

USERNAME_PATTERN = re.compile(
    r'((?<=:\/\/)(?!(?:www|community|users))[^\.]+(?=\.livejournal\.com)|'
    r'(?<=\?user=)[^&]+|'
    r'(?<=:\/\/community\.livejournal\.com\/)[^\/]+|'
    r'(?<=livejournal\.com\/users\/)[^\/]+|'
    r'(?<=livejournal\.com\/community\/)[^\/]+|'
    r'(?<=:\/\/users\.livejournal\.com\/)[^\/]+)',
    re.IGNORECASE
)

URL_SUFFIX = {
    "profile": "/profile/?socconns=friends&mode_full_socconns=1000&mode_full_comms=1000",
    "tags": "/tag",
    "photos": "/photo",
    "memories": "/memories",
}

# Photo Scraper Selectors
SEL_HEADER = 'div[class^="Header-"]'
SEL_TITLE = 'h1[class^="Title-"]'
SEL_COUNT = 'div[class^="Details-"]'
SEL_DESC = 'p[class^="Description-"]'
SEL_CONTAINER = 'a[class^="Container-"]'
SEL_PHOTO_DESC = 'p[class^="Description-"]'

