import csv
import asyncio
from pathlib import Path
from urllib.parse import unquote
from playwright.async_api import Page, Error as PlaywrightError, expect
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from .config import console, SEL_HEADER, SEL_TITLE, SEL_COUNT, SEL_DESC, SEL_CONTAINER, SEL_PHOTO_DESC

class AuthenticationError(Exception):
    """Custom exception raised when LiveJournal returns a 412 status (auth required)."""
    pass

class LiveJournalPhotoScraper:
    def __init__(self, context, headless: bool = True, image_timeout_ms: int = 15000, max_retries: int = 3, delay: float = 0.0):
        self.context = context
        self.headless = headless
        self.image_timeout = image_timeout_ms
        self.max_retries = max_retries
        self.delay = delay

    async def scrape_album(self, url: str, output_dir: Path = None) -> bool:
        """Handles the end-to-end flow for a single album URL."""
        if not url.startswith(('http://', 'https://')):
            console.log(f"[bold red]Invalid URL skipped:[/bold red] {url}")
            return False

        # Parse url parts for directory and PDF naming
        parts = url.split(r"album/")
        album_user = url.split("//")[1].split(".")[0].replace("-", "_")
        album_id = parts[1].split("/")[0] if len(parts) > 1 else "unknown"

        if output_dir:
            dir_path = Path(output_dir)
        else:
            dir_path = Path(f"{album_user}-album{album_id}")

        # Create a new page for scraping this album
        page = await self.context.new_page()
        try:
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    with console.status(f"[bold blue]Navigating to album URL... (Attempt {attempt})", spinner="earth"):
                        resp = await page.goto(url, wait_until="domcontentloaded")
                        if not resp or resp.status != 200:
                            raise Exception(f"HTTP Status {resp.status if resp else 'No Response'}")
                        await page.wait_for_timeout(2000)

                    metadata = await self._extract_metadata_and_scroll(page)

                    # Ensure target directory exists and save PDF of the scrolled page
                    dir_path.mkdir(parents=True, exist_ok=True)
                    pdf_path = dir_path / f"{album_user}_album_{album_id}.pdf"
                    
                    from .utils import download_pdf
                    console.print(f"    [bold blue]Downloading Album Page PDF...[/bold blue]")
                    if await download_pdf(page, str(pdf_path)):
                        console.print(f"    [bold green]✓[/bold green] [dim]Saved PDF:[/dim] {pdf_path}")

                    stats = await self._download_images(page, url, metadata, output_dir)

                    if stats:
                        self._print_album_summary(url, stats)
                        return True
                    return False
                except (TimeoutError, Exception) as e:
                    console.log(f"[bold yellow]Attempt {attempt} failed for {url}: {e}[/bold yellow]")
                    if attempt == max_attempts:
                        console.log(f"[bold red]Max retry attempts reached for {url}. Skipping.[/bold red]")
                        break
                    else:
                        await asyncio.sleep(2)
            return False
        finally:
            await page.close()

    async def _extract_metadata_and_scroll(self, page: Page) -> dict:
        """Extracts title, description, and image count from the DOM."""
        try:
            header = page.locator(SEL_HEADER)
            await expect(header).to_be_visible(timeout=7500)
            if not header:
                raise Exception("Album header not found. The page structure may have changed or the album may be unavailable.")
        except (AssertionError, PlaywrightError) as e:
            console.log(f"[bold red]Failed to locate album header: {e}[/bold red]")
            raise Exception("Album header not found. The page structure may have changed or the album may be unavailable.")

        title_el = header.locator(SEL_TITLE)
        try:
            # Set a short timeout (e.g., 1000ms) so your code doesn't hang if it's missing
            title = await title_el.inner_text(timeout=1000)
        except (PlaywrightError, TimeoutError):
            title = None

        count_el = header.locator(SEL_COUNT)
        try:
            count_text = await count_el.inner_text(timeout=1000)
            count_text = count_text.split(" ")[0] if count_text else "0"
        except (PlaywrightError, TimeoutError):
            raise Exception("Album details missing or empty album.")

        if title is None or title.strip() == "":
            if count_text == "0":
                console.log("[bold yellow]Album appears to be empty (0 photos) and has no title. Skipping.[/bold yellow]")
                raise Exception("Album details missing or empty album.")

        desc_el = header.locator(SEL_DESC)

        try:
            # Set a short timeout (e.g., 1000ms) so your code doesn't hang if it's missing
            desc_text = await desc_el.inner_text(timeout=1000)
        except (PlaywrightError, TimeoutError):
            desc_text = ""

        console.print(Panel(
            f"[bold]Title:[/bold] {title}\n[bold]Expected Photos:[/bold] {count_text}\n[bold]Description:[/bold] {desc_text}",
            title="[bold green]Album Detected[/bold green]",
            border_style="green",
            expand=False
        ))

        try:
            expected_count = int(count_text)
        except ValueError:
            expected_count = 0

        if expected_count > 0:
            await self._scroll_to_bottom(page)

        return {"title": title, "description": desc_text, "expected_count": expected_count}

    async def _scroll_to_bottom(self, page: Page, wait_time: int = 2, stable_checks: int = 2):
        """Scrolls dynamically until no new content loads."""
        last_height = await page.evaluate("document.body.scrollHeight")
        retries = 0

        with console.status("[bold yellow]Scrolling to load all images...", spinner="dots") as status:
            while True:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                await asyncio.sleep(wait_time)

                current_scroll = await page.evaluate("window.scrollY + window.innerHeight")
                current_height = await page.evaluate("document.body.scrollHeight")

                if current_scroll > current_height - 5:
                    status.update("[bold cyan]Hit current bottom, waiting for content...[/bold cyan]")
                    await asyncio.sleep(wait_time)

                    new_height = await page.evaluate("document.body.scrollHeight")
                    if new_height == last_height:
                        retries += 1
                        status.update(f"[bold magenta]No new content. Retrying ({retries}/{stable_checks})...[/bold magenta]")
                        await page.mouse.wheel(0, -500)

                        if retries >= stable_checks:
                            console.log("[bold green]Scroll height stable. Reached the end.[/bold green]")
                            break
                    else:
                        retries = 0
                        last_height = new_height

    async def _download_images(self, page: Page, url: str, metadata: dict, output_dir: Path = None) -> dict:
        """Extracts src attributes and downloads images with a progress bar."""
        parts = url.split(r"album/")
        album_user = url.split("//")[1].split(".")[0].replace("-", "_")
        album_id = parts[1].split("/")[0] if len(parts) > 1 else "unknown"

        if output_dir:
            dir_path = output_dir
        else:
            dir_path = Path(f"{album_user}-album{album_id}")

        dir_path.mkdir(parents=True, exist_ok=True)
        containers = await page.query_selector_all(SEL_CONTAINER)

        if not containers:
            console.log("[bold yellow]No images found on the page to download.[/bold yellow]")
            return {}

        stats = {"downloaded": 0, "failed": 0, "dir": dir_path}
        csv_file = dir_path / f"{album_user}_{album_id}.csv"

        with open(csv_file, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["album_id", "album_name", "album_desc", "url", "description"])

            with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeRemainingColumn(),
                    console=console
            ) as progress:

                task = progress.add_task("[cyan]Preparing to download...", total=len(containers))

                for body in containers:
                    img = await body.query_selector('img')
                    if not img:
                        stats["failed"] += 1
                        progress.advance(task)
                        continue

                    img_url = await img.get_attribute('src')
                    if not img_url:
                        stats["failed"] += 1
                        progress.advance(task)
                        continue

                    # Rewrite to fetch original image size suffix
                    if "_" in img_url:
                        parts_img = img_url.rsplit("_", 1)
                        suffix = parts_img[1]
                        ext_start = suffix.find(".")
                        ext = suffix[ext_start:] if ext_start != -1 else ""
                        img_url = parts_img[0] + "_original" + ext
                    
                    img_filename = Path(img_url).name
                    if not img_filename or "." not in img_filename:
                        img_filename = f"image_{stats['downloaded'] + stats['failed']}.jpg"

                    # Extract Description
                    desc_el = await body.query_selector(SEL_PHOTO_DESC)
                    desc_text = await desc_el.inner_text() if desc_el else ""
                    desc_text = desc_text.strip()

                    progress.update(task, description=f"[cyan]Downloading:[/cyan] [green]{img_filename}[/green]")

                    success = await self._fetch_and_save_image(page, img_url, dir_path / img_filename)

                    if success:
                        stats["downloaded"] += 1
                        writer.writerow([album_id, metadata.get("title", ""), metadata.get("description", ""), img_url, desc_text])
                    else:
                        stats["failed"] += 1

                    progress.advance(task)

            progress.update(task, description="[bold green]Album download complete![/bold green]")

        return stats

    async def _fetch_and_save_image(self, page: Page, img_url: str, save_path: Path) -> bool:
        """Handles the HTTP request, retries, and file writing for a single image."""
        if self.delay > 0:
            await asyncio.sleep(self.delay)
            
        for attempt in range(self.max_retries):
            try:
                resp = await page.request.get(img_url, timeout=self.image_timeout)

                if resp.status == 412:
                    raise AuthenticationError("Precondition Failed (412). You might need to log in to access these photos.")
                if resp.status in [404, 415]:
                    console.log(f"[yellow]Image not found (Status {resp.status}): {img_url}[/yellow]")
                    return False
                if resp.status != 200:
                    raise Exception(f"Status code {resp.status}")

                img_bytes = await resp.body()
                save_path.write_bytes(img_bytes)
                return True

            except AuthenticationError as e:
                raise e
            except Exception as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 * (2 ** attempt))
                else:
                    console.log(f"[bold red]Failed to download {img_url}: {e}[/bold red]")

        return False

    def _print_album_summary(self, url: str, stats: dict):
        console.print(Panel(
            f"[bold]Target URL:[/bold] {url}\n"
            f"[bold]Saved Directory:[/bold] {stats.get('dir')}\n"
            f"[bold]Successfully Downloaded:[/bold] [green]{stats.get('downloaded')}[/green]\n"
            f"[bold]Failed:[/bold] [red]{stats.get('failed')}[/red]",
            title="[bold blue]Album Processing Complete[/bold blue]",
            border_style="blue",
            expand=False
        ))
