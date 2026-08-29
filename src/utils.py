import builtins
import os
import re
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

import pymupdf
from playwright.async_api import Error as PlaywrightError, expect
from playwright.async_api import Page
from rich.console import Console
from rich.table import Table

from .config import USERNAME_PATTERN, update_status

# Force standard output streams to use UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Suppress mupdf display errors
pymupdf.TOOLS.mupdf_display_errors(False)

_original_print = builtins.print
_ui_sink = None

def strip_markup(text: str) -> str:
    """Strip Rich/Textual markup tags from a string."""
    return re.sub(r"\[/?[a-zA-Z0-9_\$# -]+\]", "", text)

def render_to_plain_text(obj) -> str:
    """Render any object or markup string to plain text for file logging."""
    if isinstance(obj, str):
        return strip_markup(obj)
    try:
        sio = StringIO()
        temp_console = Console(file=sio, force_terminal=False, color_system=None, width=120)
        temp_console.print(obj)
        return sio.getvalue().rstrip("\r\n")
    except Exception:
        return strip_markup(str(obj))

def log_to_file(text: str, log_file: str | Path = None) -> None:
    if not text:
        return
    try:
        from . import config
        target_path = Path(log_file or config.current_log_file)
        if target_path.parent:
            target_path.parent.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = "\n".join(f"[{ts}] {l}" if l.strip() else "" for l in text.splitlines())
        with open(target_path, "a", encoding="utf-8") as f:
            f.write(body + "\n")
    except Exception:
        pass

def setup_file_logging(log_file: str | Path = None, sink=None) -> None:
    """Wrap builtins.print to log to file and optionally route to a UI sink."""
    global _ui_sink
    if log_file:
        from . import config
        config.current_log_file = str(log_file)
    if sink is not None:
        _ui_sink = sink if callable(sink) else None

    def unified_print(*args, **kwargs):
        sep = kwargs.pop("sep", " ")
        kwargs.pop("end", None)
        kwargs.pop("file", None)
        kwargs.pop("flush", None)

        rendered_items = []
        plain_parts = []
        for arg in args:
            if isinstance(arg, str):
                s = arg
                for k, v in kwargs.items():
                    val = re.sub(r"(?<!\\)\[", "\\\\[", str(v))
                    val = re.sub(r"(?<!\\)]", "]", str(val))
                    s = s.replace(f"${k}", val)
                rendered_items.append(s)
                plain_parts.append(strip_markup(s))
            else:
                rendered_items.append(arg)
                plain_parts.append(render_to_plain_text(arg))

        if _ui_sink:
            _ui_sink(rendered_items)
        else:
            _original_print(*rendered_items, sep=sep)

        full_text = sep.join(plain_parts)
        if full_text.strip():
            from . import config
            log_to_file(full_text, config.current_log_file)

    builtins.print = unified_print

async def compress_pdf(input_path: str):
    """Compresses a PDF file using PyMuPDF."""
    if not Path(input_path).exists():
        return
    temp_path = input_path.replace(".pdf", "-temp.pdf")
    doc = None
    try:
        doc = pymupdf.open(input_path)
        for page in doc:
            text_rect = pymupdf.Rect()

            # 1. Loop ONLY through text blocks to find where text actually exists
            for block in page.get_text("blocks"):
                # block coordinate unpacks as (x0, y0, x1, y1, text, block_no, block_type)
                # block[4] contains the actual string content
                if block[4].strip():  # Skip blocks that contain only whitespace characters
                    text_rect |= block[:4]

            # 2. Apply the cropbox only if valid text was detected
            if text_rect.is_valid and not text_rect.is_empty:

                crop_rect = pymupdf.Rect(
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
        print(f"[bold red]Failed to compress PDF {input_path}: {e}[/bold red]")
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
        # noinspection PyTypeChecker
        await page.pdf(
            path=save_path,
            print_background=True,
            scale=0.5,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"}
        )
        return True
    except PlaywrightError as e:
        if "headless" in str(e).lower():
            print(f"    [bold $warning]⚠[/bold $warning] [dim]Skipping PDF for {Path(save_path).name} (PDF generation requires headless mode).[/dim]")
            return False
        else:
            print(f"    [bold $error]Failed to download PDF for {save_path}: {e}[/bold $error]")
            raise e
    except Exception as e:
        print(f"    [bold $error]Failed to download PDF for {save_path}: {e}[/bold $error]")
        raise e

async def download_html(page: Page, save_path: str):
    """Downloads the current page HTML content."""
    Path(save_path).write_text(await page.content(), encoding="utf-8")

async def scroll_with_keyboard(page: Page, mem_count: int, max_memories: int):
    if mem_count > max_memories:
        print(
            f"    [bold $warning]⚠[/bold $warning] [dim]Memory count ({mem_count}) exceeds max_memories, collecting the index and the first {max_memories} memories..."
        )

    """Scrolls down using the lazyloader/footer or keyboard to load all dynamic content/entries."""
    no_more_entries = page.locator(".b-lenta-emptiness")

    # Cast mem_count to int immediately to prevent bad string/numeric comparisons
    try:
        target_count = int(mem_count) if mem_count else 0
    except (ValueError, TypeError):
        target_count = 0

    target_str = target_count if target_count > 0 else "unknown"

    update_status(f"Scrolling...[dim] Target: [$text-secondary]{target_str}[/$text-secondary][/dim]")
    entry_count = len(await page.locator('.b-lenta-body > article').all())

    # ensure target_count is greater than 0 so the loop condition evaluates safely
    while not await no_more_entries.is_visible() and (target_count == 0 or entry_count < target_count):
        # Define candidate elements that represent the bottom of the active content or the loader itself.
        # We scroll these into view so they are visible on screen, triggering the lazyloader,
        # without scrolling past them into the blank whitespace at the very end of the page.
        loader = page.locator('.b-lenta-loader').last
        footer = page.locator('.b-lenta-footer').last
        last_article = page.locator('.b-lenta-body > article').last

        scrolled = False
        # Try scrolling the loader container into view
        if await loader.count() > 0:
            try:
                await loader.scroll_into_view_if_needed(timeout=1000)
                scrolled = True
            except Exception:
                pass
        
        # If loader scroll didn't work or isn't present, try the footer
        if not scrolled and await footer.count() > 0:
            try:
                await footer.scroll_into_view_if_needed(timeout=1000)
                scrolled = True
            except Exception:
                pass

        # If footer scroll didn't work or isn't present, try the last loaded article
        if not scrolled and await last_article.count() > 0:
            try:
                await last_article.scroll_into_view_if_needed(timeout=1000)
                scrolled = True
            except Exception:
                pass

        # Fallback: if we couldn't find/scroll those elements, use PageDown or scroll down step-by-step
        if not scrolled:
            await page.keyboard.press("PageDown")

        await page.wait_for_timeout(2000)

        current_count = len(await page.locator('.b-lenta-body > article').all())
        
        # If the count didn't change, try a PageDown press as an extra kick to make sure
        # the lazy loader triggers (in case scroll_into_view_if_needed aligned it slightly off screen)
        if current_count == entry_count:
            await page.keyboard.press("PageDown")
            await page.wait_for_timeout(1000)
            current_count = len(await page.locator('.b-lenta-body > article').all())

        if current_count != entry_count:
            entry_count = current_count
            loaded_str = f"{current_count}/{target_str}" if mem_count else str(current_count)
            update_status(f"Scrolling... [dim]Loaded [/dim][$text-secondary]{loaded_str}[/$text-secondary] [dim]entries[/dim]")

async def check_for_tags(page: Page, timeout: int) -> bool:
    try:
        await page.locator("a[href*='/feed'], a[href*='/profile'], a[href*='/calendar']").first.wait_for(state="visible", timeout=timeout)
        return len(await page.locator('a[href*="/tag"]').all()) != 0
    except (PlaywrightError, TimeoutError):
        return False

async def check_for_memories(page: Page, timeout: int) -> bool:

    memories = page.locator('div.b-lenta-body > article')
    no_mems = page.get_by_text('No more entries')

    combined_locator = memories.or_(no_mems)
    try:
        await expect(combined_locator.first).to_be_visible(timeout=timeout)
        return len(await memories.all()) != 0
    except (PlaywrightError, TimeoutError):
        return False

async def check_for_vgifts(page: Page, timeout: int) -> bool:
    try:
        await page.get_by_text("a virtual gift").wait_for(state="visible", timeout=timeout)
        return len(await page.locator('.b-vgifts').all()) != 0
    except (PlaywrightError, TimeoutError):
        return False

async def check_for_userpics(page: Page, timeout: int) -> bool:
    combined_sel = page.get_by_text("Current Userpics").or_(page.get_by_text("No Pictures"))

    try:
        await expect(combined_sel.first).to_be_visible(timeout=timeout)
        return len(await page.get_by_text("No Pictures").all()) == 0
    except (PlaywrightError, TimeoutError):
        return False

async def check_for_albums(page: Page, timeout: int) -> bool:
    try:
        await page.locator('h1').nth(0).wait_for(state="attached", timeout=timeout)
        return len(await page.get_by_text("No Albums").all()) == 0
    except (PlaywrightError, TimeoutError):
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
            print(f"[bold red]Failed to read input file {target_str}: {e}[/bold red]")
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
            return "[$success]✓[/$success]"
        elif status == "failed":
            return "[red]✗[/red]"
        return "[dim]-[/dim]"

    for user in all_users:
        has_failures = "failed" in user.results.values()
        status = "[red]✗ Failed[/red]" if has_failures else "[$success]✓ Success[/$success]"

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

    print("\n")
    print(table)

async def get_account_type(page: Page) -> str:
    try:
        await page.locator('.ljuser').first.wait_for(state="attached")
        account_type = await page.locator('.ljuser').first.get_attribute('class')
        if account_type and "i-ljuser-type-P" in account_type:
            return "personal"
        elif account_type and "i-ljuser-type-C" in account_type:
            return "community"
        else:
            raise
    except Exception:
        print("[bold $text-warning]Warning![/bold]Could not determine account type.[/$text-warning]")
        return ""

async def get_logged_in(page) -> str:
    """Checks if the user is logged in."""
    try:
        if await page.get_by_text("JOIN FREE").is_visible():
            return ""
        else:
            user = (await page.locator('.s-header-item__link--user').text_content()).strip()
            return user if user else ""
    except Exception:
        return ""

