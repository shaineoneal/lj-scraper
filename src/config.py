import json
import re
from pathlib import Path

from rich.console import Console

console = Console()

def update_status(text: str) -> None:
    """Update the current status if the TUI has hooked console.update_status."""
    updater = getattr(console, "update_status", None)
    if callable(updater):
        updater(text)
    elif text:
        console.print(f"[dim]{text}[/dim]")

CONFIG_FILE = Path("config.json")

DEFAULT_SETTINGS = {
    "user_data_dir": "user_profile",
    "max_memories": 750,
    "max_dl_memories": 500,
    "delay": 3.0,
    "timeout": 30,
    "entries": "both",
    "profile": "both",
    "tags": "both",
    "userpics": "both",
    "vgifts": "both",
    "memories": "both",
    "photos": "both"
}

def load_config(path: Path = CONFIG_FILE) -> dict:
    """Loads configuration from config file.
    If the file doesn't exist, it creates a default template.
    """
    path = Path(path) if path else CONFIG_FILE
    if not path.exists():
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTINGS, f, indent=4)
        except Exception as e:
            console.print(f"[bold yellow]Warning: Could not create default config file: {e}[/bold yellow]")
        return {**DEFAULT_SETTINGS}

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
            # Merge with defaults to ensure all keys are present
            merged = {**DEFAULT_SETTINGS, **user_config}
            return merged
    except Exception as e:
        console.print(f"[bold red]Warning: Failed to parse {path}, using defaults: {e}[/bold red]")
        return {**DEFAULT_SETTINGS}

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

