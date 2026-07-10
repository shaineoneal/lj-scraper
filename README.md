# LiveJournal Scraper

A robust, modular command-line tool built on Playwright to scrape and archive LiveJournal profiles, entries, tags, userpics, virtual gifts, memories, and photo albums. It exports web pages as both raw HTML files and compressed, print-ready PDFs.

## Features

- **Full Profile Backups**: Automatically archives user profile pages, tags, memories, virtual gifts, and userpics.
- **Entries & Comments**: Scrolls and captures recent entries dynamically.
- **Photo Album Archiving**: Recursively crawls photo albums, saving high-resolution images locally along with a PDF of the album grid.
- **HTML & PDF Formats**: Saves pages in both raw HTML and optimized, compressed PDF formats (using PyMuPDF compression).
- **Session Preservation (Login Flow)**: Includes a login utility to save browser cookies/sessions, allowing you to scrape private, locked, or friends-only posts.
- **Batch Processing**: Accepts profile URLs, usernames, album URLs, or a `.txt` file containing a mix of all targets.
- **Graceful Retries**: Automatically retries failed page loads or timeouts.
- **Rich CLI**: Beautiful terminal outputs and tables courtesy of `rich`.

---

## Installation & Setup

### Option 1: Download Pre-compiled Binary (Easiest)
If you don't want to set up a Python environment, you can download the compiled executable (`lj_scraper` for Linux or `lj_scraper.exe` for Windows) directly from the **Releases** tab of your GitHub repository.

### Option 2: Running from Source (Development)

1. **Clone the Repository**:
   ```bash
   git clone <your-repo-url>
   cd lj-scraper
   ```

2. **Set up a Virtual Environment**:
   ```bash
   python -m venv .venv
   # Activate on Windows:
   .venv\Scripts\activate
   # Activate on Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install Package & Dependencies**:
   ```bash
   pip install --upgrade pip
   pip install .
   ```

4. **Install Playwright Browsers**:
   Since the scraper uses Playwright to render pages dynamically, install the required Chromium browser binary:
   ```bash
   playwright install chromium
   ```

---

## Quick Start & Usage

Once installed, the CLI tool is registered as `lj-scraper`. You can also run it via python: `python lj_scraper.py`.

### 1. Authenticating (Optional but recommended for locked posts)
To access private profiles or friends-only entries, launch the login flow to authenticate. This will open a browser window for you to log in to LiveJournal and save the session credentials to your local user profile.
```bash
lj-scraper --login
```
*(Use `--user-data-dir <path>` if you want to store credentials in a specific folder).*

### 2. Scraping Profiles
To scrape everything (profile, entries, tags, memories, gifts, and photos) for a username or profile URL:
```bash
lj-scraper username
# or
lj-scraper https://username.livejournal.com
```

### 3. Scraping Standalone Photo Albums
To download photos from a specific album:
```bash
lj-scraper https://username.livejournal.com/photo/album/12345
```

### 4. Batch Scraping from a Text File
You can provide a text file containing a list of usernames, profile URLs, or photo album URLs (one per line):
```bash
lj-scraper targets.txt
```

---

## CLI Reference & Flags

```text
usage: lj-scraper [-h] [--config CONFIG] [--user-data-dir USER_DATA_DIR]
                  [--login [LOGIN]] [--headed [HEADED]] [--headless]
                  [--delay DELAY] [--install-deps [INSTALL_DEPS]]
                  [--entries [{html,pdf,both,none} ...]]
                  [--profile [{html,pdf,both,none} ...]]
                  [--tags [{html,pdf,both,none} ...]]
                  [--userpics [{html,pdf,both,none} ...]]
                  [--vgifts [{html,pdf,both,none} ...]]
                  [--memories [{html,pdf,both,none} ...]]
                  [--photos [{html,pdf,both,none} ...]]
                  [--max-memories [MAX_MEMORIES]]
                  [--max-dl-memories [MAX_DL_MEMORIES]]
                  [target]

Scrape and download LiveJournal accounts and photo albums.

positional arguments:
  target                A LiveJournal profile URL, username, photo album URL, or .txt file containing them.

options:
  -h, --help            show this help message and exit
  --config CONFIG       Path to a JSON config file to load settings from (default: config.json).
  --user-data-dir USER_DATA_DIR
                        Directory for browser session data (default: read from USER_DATA_DIR env var or 'user_profile')
  --login [LOGIN]       Launch browser to log in manually and save session credentials.
  --headed [HEADED]     Run browser in headed mode (visible window).
  --headless            Run browser in headless mode.
  --delay DELAY         Time in seconds to wait before page actions or downloads (default: 0.0)
  --install-deps [INSTALL_DEPS]
                        Install missing Linux system dependencies for Playwright.
  --max-memories [MAX_MEMORIES]
                        Maximum number of memories to scrape (default: 750).
  --max-dl-memories [MAX_DL_MEMORIES]
                        Maximum number of memories to download (default: 500).

Selective profile scraping flags:
  If none of these are selected, the tool scrapes all components by default.
  If any are selected, only the specified components are scraped.
  Optional format values: 'html', 'pdf', 'both', or 'none'. If a format is set,
  other formats are turned off (e.g., '--entries pdf' saves PDF only).
  If no format argument is specified (e.g. '--entries'), all configured formats run.

  --entries [{html,pdf,both,none} ...]
                        Scrape and download entries.
  --profile [{html,pdf,both,none} ...]
                        Scrape and download user profile.
  --tags [{html,pdf,both,none} ...]
                        Scrape and download tags.
  --userpics [{html,pdf,both,none} ...]
                        Scrape and download userpics.
  --vgifts [{html,pdf,both,none} ...]
                        Scrape and download virtual gifts.
  --memories [{html,pdf,both,none} ...]
                        Scrape and download memories.
  --photos [{html,pdf,both,none} ...]
                        Scrape and download photo albums and photos.
```

### Examples

* **Only scrape profile metadata (both HTML and PDF) and entries (PDF only):**
  ```bash
  lj-scraper username --profile --entries pdf
  ```

* **Only scrape tags in HTML format:**
  ```bash
  lj-scraper username --tags html
  ```

* **Run a slow scrape with a 1.5-second delay to avoid rate-limiting:**
  ```bash
  lj-scraper username --delay 1.5
  ```

---

## Output Directory Structure

Files are organized in folders corresponding to usernames and albums in your working directory.

```text
.
└── username/                        # Folder for the scraped user
    ├── username - Profile.html      # Profile details in HTML
    ├── username - Profile.pdf       # Profile details in PDF
    ├── username - Tags.pdf          # List of tags (PDF-only)
    ├── username - Userpics.html     # User avatar listing
    ├── username - Virtual Gifts.html
    ├── Recent Entries Title.pdf     # Scraped posts/comments
    │
    └── photos/                      # Subfolder containing photo albums
        └── album_12345/             # Directory for a specific album
            ├── username_12345.csv   # Photo metadata index (CSV format)
            ├── username_album_12345.pdf # Album grid layout PDF
            ├── image_1.jpg          # Downloaded raw images
            └── image_2.jpg
```

*Note: PDF generation requires the scraper to be run in headless mode (default). If `--headed` is set, PDF generation will be skipped, but HTML files and images will still be saved.*

---

## Local Compilation / Packaging

To compile the application into a standalone executable on your own machine:

1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```

2. Compile using the spec file:
   ```bash
   pyinstaller lj_scraper.spec
   ```

The compiled binaries will be outputted to the `dist/` directory.
