import os
import re
import sys
from pathlib import Path
from contextlib import asynccontextmanager
import pymupdf as fitz
from playwright.async_api import Page, expect, Error as PlaywrightError
from rich.spinner import Spinner
from rich.live import Live
from rich.table import Table
from .config import console, USERNAME_PATTERN

# Force standard output streams to use UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Suppress mupdf display errors
fitz.TOOLS.mupdf_display_errors(False)

@asynccontextmanager
async def initialize_spinner(text: str, spinner_type: str = "dots"):
    """Initializes a Rich spinner and binds it to a Live display."""
    spinner_instance = Spinner(spinner_type, text=f"[bold blue]{text}[/bold blue]")
    with Live(spinner_instance, console=console, refresh_per_second=10, transient=True):
        yield spinner_instance

async def compress_pdf(input_path: str):
    """Compresses a PDF file using PyMuPDF."""
    if not Path(input_path).exists():
        return
    temp_path = input_path.replace(".pdf", "-temp.pdf")
    doc = None
    try:
        doc = fitz.open(input_path)
        for page in doc:
            text_rect = fitz.Rect()

            # 1. Loop ONLY through text blocks to find where text actually exists
            for block in page.get_text("blocks"):
                # block coordinate unpacks as (x0, y0, x1, y1, text, block_no, block_type)
                # block[4] contains the actual string content
                if block[4].strip():  # Skip blocks that contain only whitespace characters
                    text_rect |= block[:4]

            # 2. Apply the cropbox only if valid text was detected
            if text_rect.is_valid and not text_rect.is_empty:
                # Add a 15-point padding buffer so text doesn't touch the canvas frame
                padding = 15

                crop_rect = fitz.Rect(
                    0,
                    0,
                    page.rect.width,
                    min(page.rect.height, text_rect.y1 + 10)
                )

                # Crop the page viewport. Backgrounds inside this frame stay intact.
                page.set_cropbox(crop_rect)
        doc.save(
            temp_path,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            garbage=4,
            use_objstms=True,
            clean=True,
            compression_effort=100
        )
        doc.close()
        os.replace(temp_path, input_path)
    except Exception as e:
        console.print(f"[bold red]Failed to compress PDF {input_path}: {e}[/bold red]")
        if doc:
            doc.close()
        if os.path.exists(temp_path):
            os.remove(temp_path)

async def download_pdf(page: Page, save_path: str) -> bool:
    """Downloads the current page as a PDF file. Returns True if successful, False otherwise."""
    try:
        await page.emulate_media(media="screen")
        await page.add_style_tag(content="*, html, body { min-height: 0 !important; max-height: none !important; }")
        await page.wait_for_timeout(1000)
        await page.evaluate('''() => {
            document.querySelectorAll('img[loading="lazy"]').forEach(img => img.setAttribute('loading', 'eager'));
        }''')
        if os.path.exists(save_path):
            os.remove(save_path)
        await page.pdf(
            path=save_path,
            print_background=True,
            scale=0.5,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"}
        )
        return True
    except PlaywrightError as e:
        if "headless" in str(e).lower():
            console.print(f"    [bold yellow]⚠[/bold yellow] [dim]Skipping PDF for {Path(save_path).name} (PDF generation requires headless mode).[/dim]")
            return False
        else:
            console.log(f"[bold red]Failed to download PDF for {save_path}: {e}[/bold red]")
            raise e
    except Exception as e:
        console.log(f"[bold red]Failed to download PDF for {save_path}: {e}[/bold red]")
        raise e

async def download_html(page: Page, save_path: str):
    """Downloads the current page HTML content."""
    Path(save_path).write_text(await page.content(), encoding="utf-8")

async def scroll_with_keyboard(page: Page, spinner: Spinner, mem_count=None):
    """Scrolls down using End key to load all dynamic content/entries."""
    no_more_entries = page.get_by_text("No more entries")
    target = mem_count if mem_count and mem_count != "0" else "unknown"

    spinner.update(text=f"[bold blue]Scrolling...[/bold blue][dim] Target: [/dim][blue]{target}[/blue]")
    entry_count = len(await page.locator('.b-lenta-body > article').all())

    while not await no_more_entries.is_visible():
        await page.keyboard.down("End")
        await page.wait_for_timeout(2000)
        await page.keyboard.up("End")

        current_count = len(await page.locator('.b-lenta-body > article').all())
        if current_count != entry_count:
            entry_count = current_count
            loaded_str = f"{current_count}/{target}" if mem_count else str(current_count)
            spinner.update(text=f"[bold blue]Scrolling...[/bold blue][dim] Loaded [/dim][blue]{loaded_str}[/blue] [dim]entries[/dim]")

async def check_for_tags(page: Page, timeout: int = 7500) -> bool:
    try:
        await page.get_by_text(re.compile(r"Tags|Entries|Archive|Profile", re.IGNORECASE)).wait_for(state="attached", timeout=timeout)
        return len(await page.locator('a[href*="/tag"]').all()) != 0
    except (PlaywrightError, TimeoutError):
        return False

async def check_for_memories(page: Page, timeout: int = 7500) -> bool:
    try:
        await page.locator('div.b-lenta-body > article').nth(0).wait_for(state="attached", timeout=timeout)
        return True
    except (AssertionError, PlaywrightError):
        return False

async def check_for_vgifts(page: Page, timeout: int = 7500) -> bool:
    try:
        await page.get_by_text("a virtual gift").wait_for(state="visible")
        return len(await page.locator('.b-vgifts').all()) != 0
    except (PlaywrightError, TimeoutError):
        return False

async def check_for_userpics(page: Page, timeout: int = 7500) -> bool:
    try:
        await page.locator('h1').nth(1).wait_for(state="attached", timeout=timeout)
        return len(await page.get_by_text("No Pictures").all()) == 0
    except PlaywrightError:
        return False

def parse_targets(target_str: str) -> tuple[list[str], list[str]]:
    """Parses a target string (URL, username, or file) and returns (profile_targets, album_targets)."""
    if not target_str:
        return [], []

    profile_targets = []
    album_targets = []

    def process_item(item: str):
        item = item.strip()
        if not item:
            return
        if item.startswith(("http://", "https://")):
            if "livejournal.com" in item and "/photo" in item and "/album" in item:
                album_targets.append(item)
            else:
                match = re.search(USERNAME_PATTERN, item)
                if match:
                    username = match.group(0).replace("-", "_")
                    profile_targets.append(username)
        else:
            profile_targets.append(item.replace("-", "_"))

    if target_str.endswith(".txt"):
        try:
            lines = Path(target_str).read_text(encoding="utf-8").splitlines()
            for line in lines:
                process_item(line)
        except Exception as e:
            console.print(f"[bold red]Failed to read input file {target_str}: {e}[/bold red]")
    else:
        process_item(target_str)

    # De-duplicate while preserving order
    unique_profiles = list(dict.fromkeys(profile_targets))
    unique_albums = list(dict.fromkeys(album_targets))

    return unique_profiles, unique_albums

def print_summary_table(all_users: list, elapsed_time: float):
    """Renders a beautiful Rich table summarizing the batch run."""
    table = Table(title=f"Scraping Summary (Took {elapsed_time:.1f}s)", box=None, show_lines=True)
    table.add_column("Status", justify="center")
    table.add_column("Account", style="cyan", no_wrap=False)
    table.add_column("Entries", justify="center")
    table.add_column("Profile", justify="center")
    table.add_column("Tags", justify="center")
    table.add_column("Userpics", justify="center")
    table.add_column("Virtual Gifts", justify="center")
    table.add_column("Memories", justify="center")
    table.add_column("Photos", justify="center")

    def format_icon(status: str) -> str:
        if status == "success":
            return "[green]✓[/green]"
        elif status == "failed":
            return "[red]✗[/red]"
        return "[dim]-[/dim]"

    for user in all_users:
        has_failures = "failed" in user.results.values()
        status = "[red]✗ Failed[/red]" if has_failures else "[green]✓ Success[/green]"

        table.add_row(
            status,
            user.username,
            format_icon(user.results.get("entries", "skipped")),
            format_icon(user.results.get("profile", "skipped")),
            format_icon(user.results.get("tags", "skipped")),
            format_icon(user.results.get("userpics", "skipped")),
            format_icon(user.results.get("vgifts", "skipped")),
            format_icon(user.results.get("memories", "skipped")),
            format_icon(user.results.get("photos", "skipped"))
        )

    console.print("\n")
    console.print(table)

async def get_account_type(page: Page) -> str:
    try:
        await page.locator('.ljuser').first.wait_for(state="attached")
        account_type = await page.locator('.ljuser').first.get_attribute('class')
        if "i-ljuser-type-P" in account_type:
            return "personal"
        elif "i-ljuser-type-C" in account_type:
            return "community"
    except Exception:
        console.print("[bold yellow]Could not determine account type.[/bold yellow]")
        return None
