import asyncio
import csv
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, expect

from .config import (
    SEL_CONTAINER,
    SEL_COUNT,
    SEL_DESC,
    SEL_HEADER,
    SEL_PHOTO_DESC,
    SEL_TITLE,
    update_status,
)


class AuthenticationError(Exception):
    """Custom exception raised when LiveJournal returns a 412 status (auth required)."""
    pass

class LiveJournalPhotoScraper:
    def __init__(self, context, settings):
        self.context = context
        self.settings = settings
        self.max_retries = 3

    async def scrape_album(self, url: str, output_dir = None) -> bool:
        """Handles the end-to-end flow for a single album URL."""
        if not url.startswith(('http://', 'https://')):
            print(f"[bold $text-error]Invalid URL skipped:[/bold $text-error] {url}")
            return False

        # Parse url parts for directory and PDF naming
        parts = url.split(r"album/")
        username = url.split("//")[1].split(".")[0]
        album_user = username.replace("-", "_")
        album_id = parts[1].split("/")[0] if len(parts) > 1 else "unknown"

        if output_dir:
            dir_path = Path(output_dir)
        else:
            dir_path = Path(username) / "photos" / f"album_{album_id}"

        # Create a new page for scraping this album
        page = await self.context.new_page()
        try:
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    update_status(f"Navigating to album URL... (Attempt {attempt})")
                    resp = await page.goto(url, wait_until="domcontentloaded")
                    if not resp or resp.status != 200:
                        raise Exception(f"HTTP Status {resp.status if resp else 'No Response'}")
                    await page.wait_for_timeout(2000)

                    metadata = await self._extract_metadata_and_scroll(page)

                    # Ensure target directory exists and save PDF of the scrolled page
                    dir_path.mkdir(parents=True, exist_ok=True)
                    pdf_path = dir_path / f"{album_user}_album_{album_id}.pdf"

                    from .utils import download_pdf
                    update_status("Downloading Album Page PDF...")
                    if await download_pdf(page, str(pdf_path)):
                        print(f"        [$text-success]✓[/$text-success] [dim]Saved PDF:[/dim] {pdf_path}")

                    stats = await self._download_images(page, url, metadata, output_dir)

                    if stats:
                        #self._print_album_summary(url, stats)
                        return True
                    return False
                except (TimeoutError, Exception) as e:
                    if "AuthenticationError" in type(e).__name__:
                        raise e
                    print(f"[bold yellow]Attempt {attempt} failed for {url}: {e}[/bold yellow]")
                    if attempt == max_attempts:
                        print(f"[bold $text-error]Max retry attempts reached for {url}. Skipping.[/bold $text-error]")
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
            print(f"[bold $text-error]Failed to locate album header: {e}[/bold $text-error]")
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
                print("[$text-warning]Album appears to be empty (0 photos) and has no title. Skipping.[/$text-warning]")
                raise Exception("Album details missing or empty album.")

        desc_el = header.locator(SEL_DESC)

        try:
            # Set a short timeout (e.g., 1000ms) so your code doesn't hang if it's missing
            desc_text = await desc_el.inner_text(timeout=1000)
        except (PlaywrightError, TimeoutError):
            desc_text = ""

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

        update_status("Scrolling to load all images...")
        while True:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(wait_time)
            current_scroll = await page.evaluate("window.scrollY + window.innerHeight")
            current_height = await page.evaluate("document.body.scrollHeight")
            if current_scroll > current_height - 5:
                update_status("Hit current bottom, waiting for content...")
                await asyncio.sleep(wait_time)

                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    retries += 1
                    update_status(f"No new content. Retrying ({retries}/{stable_checks})...")
                    await page.mouse.wheel(0, -500)

                    if retries >= stable_checks:
                        update_status("Scroll height stable. Reached the end.")
                        break
                else:
                    retries = 0
                    last_height = new_height

    async def _download_images(self, page: Page, url: str, metadata: dict, output_dir: Path) -> dict:
        """Extracts src attributes and downloads images with a progress bar."""
        parts = url.split(r"album/")
        username = url.split("//")[1].split(".")[0]
        album_user = username.replace("-", "_")
        album_id = parts[1].split("/")[0] if len(parts) > 1 else "unknown"

        if output_dir:
            dir_path = output_dir
        else:
            dir_path = Path(username) / "photos" / f"album_{album_id}"

        dir_path.mkdir(parents=True, exist_ok=True)
        containers = await page.query_selector_all(SEL_CONTAINER)

        if not containers:
            print("[$text-warning]No images found on the page to download.[/$text-warning]")
            return {}

        stats = {"downloaded": 0, "failed": 0, "dir": dir_path}
        csv_file = dir_path / f"{album_user}_{album_id}.csv"

        with open(csv_file, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["album_id", "album_name", "album_desc", "url", "description"])

            update_status("Downloading...")

            for body in containers:
                try:
                    update_status(f"Processing image {stats['downloaded'] + stats['failed'] + 1}/{len(containers)}...")
                    img = await body.query_selector('img')
                    if not img:
                        stats["failed"] += 1
                        continue
                    img_url = await img.get_attribute('src')
                    if not img_url:
                        stats["failed"] += 1
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

                    success = await self._fetch_and_save_image(page, img_url, dir_path / img_filename)

                    if success:
                        stats["downloaded"] += 1
                        writer.writerow([album_id, metadata.get("title", ""), metadata.get("description", ""), img_url, desc_text])
                    else:
                        stats["failed"] += 1

                except Exception as e:
                    print(f"[bold $text-error]Error during image download: {e}[/bold $text-error]")
                    update_status("[$text-error]Album download encountered errors.[/$text-error]")

        update_status("[$text-success]Album download complete![/$text-success]")

        return stats

    async def _fetch_and_save_image(self, page: Page, img_url: str, save_path: Path) -> bool:
        """Handles the HTTP request, retries, and file writing for a single image."""

        for attempt in range(self.max_retries):
            try:
                resp = await page.request.get(img_url, timeout=self.settings.get("timeout", 30) * 1000)

                if resp.status == 412:
                    raise AuthenticationError("Precondition Failed (412). You might need to log in to access these photos.")
                if resp.status in [404, 415]:
                    print(f"        [$text-warning]Image not found (Status {resp.status}): {img_url}[/$text-warning]")
                    return False
                if resp.status != 200:
                    raise Exception(f"Status code {resp.status}")

                img_bytes = await resp.body()
                save_path.write_bytes(img_bytes)
                return True

            except AuthenticationError as e:
                print(f"[bold $text-error]Authentication Error ({e})[/bold $text-error]")
                raise e
            except Exception as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 * (2 ** attempt))
                else:
                    print(f"[bold $text-error]Failed to download {img_url}: {e}[/bold $text-error]")

        return False
