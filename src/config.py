import os
import re
from pathlib import Path
from rich.console import Console

console = Console()

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

