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
    "max_memories": 750,
    "max_dl_memories": 500,
    "login": False,
    "headed": False,
    "delay": None,
    "entries": "both",
    "profile": "both",
    "tags": "both",
    "userpics": "both",
    "vgifts": "both",
    "memories": "both",
    "photos": "both"
}

def deep_merge(dict1: dict, dict2: dict) -> dict:
    """Recursively merges dict2 into dict1."""
    for key, value in dict2.items():
        if isinstance(value, dict) and key in dict1 and isinstance(dict1[key], dict):
            deep_merge(dict1[key], value)
        else:
            dict1[key] = value
    return dict1

def load_config(path: Path = CONFIG_FILE) -> dict:
    """Loads configuration from config file.
    If the file doesn't exist, it creates a default template.
    """
    import copy
    path = Path(path)
    if not path.exists():
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTINGS, f, indent=4)
        except Exception as e:
            console.print(f"[bold yellow]Warning: Could not create default config file: {e}[/bold yellow]")
        return copy.deepcopy(DEFAULT_SETTINGS)

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
            # Merge with defaults to ensure all keys are present
            merged = copy.deepcopy(DEFAULT_SETTINGS)
            deep_merge(merged, user_config)
            return merged
    except Exception as e:
        console.print(f"[bold red]Warning: Failed to parse {path}, using defaults: {e}[/bold red]")
        return copy.deepcopy(DEFAULT_SETTINGS)

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

