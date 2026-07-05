import os
from os import mkdir
from pathlib import Path
from playwright.async_api import Page, Error as PlaywrightError
from rich.spinner import Spinner
from .config import console, URL_SUFFIX
from .utils import (
    initialize_spinner,
    download_pdf,
    download_html,
    compress_pdf,
    scroll_with_keyboard,
    check_for_tags,
    check_for_memories,
    check_for_vgifts,
    check_for_userpics,
    get_account_type
)

class LiveJournalAccount:
    """Represents a LiveJournal user and manages their specific scraping tasks."""

    def __init__(self, context, username: str, options: dict, delay: float = 7.5):
        self.context = context
        self.username = username
        self.account_type = None
        self.options = options
        import random
        self.jitter = random.uniform(0.5, 1.5) * delay
        self.user_dir = Path(f"output/{username}")
        self.is_retrying = False
        self.timeout = 30   #TODO: Fix
        self.delay = delay


        clean_username = username.replace("_", "-")
        self.base_url = f"https://{clean_username}.livejournal.com"
        self.urls = {
            "entries": self.base_url,
            "profile": f"https://www.users.livejournal.com/{clean_username}/profile",
            "tags": f"{self.base_url}{URL_SUFFIX['tags']}",
            "userpics": f"https://www.livejournal.com/allpics.bml?user={clean_username}",
            "vgifts": f"https://www.livejournal.com/manage/vgift.bml?u={clean_username}",
            "memories": f"{self.base_url}{URL_SUFFIX['memories']}",
            "photos": f"{self.base_url}{URL_SUFFIX['photos']}"
        }

        self.results = {
            "username": self.username,
            "entries": "skipped",
            "profile": "skipped",
            "tags": "skipped",
            "userpics": "skipped",
            "vgifts": "skipped",
            "memories": "skipped",
            "photos": "skipped"
        }
        self.has_run_user_info = False

    async def _fetch_page(self, url: str, max_attempts: int = 7, status_or_spinner = None) -> Page | None:
        """Navigates to the given URL with retries and returns the active page object."""
        import asyncio
        await asyncio.sleep(self.jitter)
            
        attempt = 0
        timeout_ms = int(self.timeout * 2.25 * 1000) if self.is_retrying else int(self.timeout * 1000)

        while attempt < max_attempts:
            try:
                msg = f"[bold blue]Navigating to {url}... (Attempt {attempt + 1})[/bold blue]"
                if status_or_spinner:
                    status_or_spinner.update(text=msg)

                page = await self.context.new_page()
                page.set_default_timeout(timeout_ms)
                page.set_default_navigation_timeout(timeout_ms)
                resp = await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)

                if resp and resp.status != 200:
                    if resp.status == 404:
                        attempt = max_attempts  # Don't retry on 404s
                        if 'photo' in url:
                            return None
                    raise Exception(f"HTTP Status {resp.status}", resp.status)

                if not self.has_run_user_info:
                    self.has_run_user_info = True
                    await self.run_once_per_user(page)
                    if self.account_type == "community" and 'photo' in url:
                        raise Exception("Community account detected, skipping photo albums.")
                return page

            except TimeoutError as e:
                if 'page' in locals() and page:
                    await page.close()
                attempt += 1
                if attempt >= max_attempts:
                    raise e
        return None

    async def _scrape_task(self, task_name: str, label: str, check_fn=None, save_fn=None) -> dict:
        """Generic task runner that standardizes the fetch/check/save lifecycle."""
        result = {"html": False, "pdf": False, "success": False, "error": None}
        url = self.urls[task_name]
        page = None
        async with initialize_spinner(f"Preparing to scrape {label}...") as spinner:
            try:
                page = await self._fetch_page(url, status_or_spinner=spinner)
                if check_fn and not await check_fn(page, int(self.delay * 1000)):
                    if task_name != "photos" and self.account_type != "community":
                        console.print(f"    [bold yellow]⚠[/bold yellow] [dim]No {label} found for {self.username}, skipping.[/dim]")
                    return result

                if save_fn:
                    await save_fn(page, spinner, result)
                result["success"] = True
            except Exception as e:
                if task_name != "photos" and account_type != "community":
                    console.print(f"    [bold red]✗[/bold red] [dim]Failed:[/dim] {url} - {str(e)}")
                    result["error"] = str(e)
            finally:
                if page:
                    await page.close()
        return result

    async def _save_page_assets(self, page, spinner, label, filename, res) -> None:
        """Helper to download both HTML and PDF, compress the PDF, and update results."""
        save_path = self.user_dir / filename

        try:
            spinner.update(text="[bold blue]Downloading HTML...[/bold blue]")
            await download_html(page, f"{save_path}.html")
            res["html"] = True

            spinner.update(text="[bold blue]Downloading PDF...[/bold blue]")
            if await download_pdf(page, f"{save_path}.pdf"):
                res["pdf"] = True
                spinner.update(text="[bold blue]Compressing PDF...[/bold blue]")
                await compress_pdf(f"{save_path}.pdf")
        except Exception as e:
            console.print(f"    [bold red]✗[/bold red] [dim]Error saving assets for {label}:[/dim] {e}")

        if label is not self.urls["photos"]:
            console.print(f"    [bold green]✓[/bold green] [dim]Saved HTML & PDF:[/dim] {label}")

    async def scrape_entries(self) -> dict:
        async def save(page, spinner, res):
            title = await page.title()
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c in ' -_']).rstrip()
            safe_title = safe_title or f"{self.username} - Recent Entries"
            
            await self._save_page_assets(page, spinner, self.urls['entries'], safe_title, res)

        return await self._scrape_task("entries", "recent entries", save_fn=save)

    async def scrape_profile(self) -> dict:
        async def save(page, spinner, res):
            filename = f"{self.username} - Profile"
            await self._save_page_assets(page, spinner, self.urls['profile'], filename, res)
            
            memory_count = await page.locator('.b-profile-stat-memcount > .b-profile-stat-value').all_inner_texts()
            res["mem_count"] = int(memory_count[0].replace(',', '')) if memory_count else 0

        return await self._scrape_task("profile", "profile", save_fn=save)

    async def scrape_tags(self) -> dict:
        async def save(page, spinner, res):
            filename = f"{self.username} - Tags"
            await self._save_page_assets(page, spinner, self.urls['tags'], filename, res)

        return await self._scrape_task("tags", "tags", check_fn=check_for_tags, save_fn=save)

    async def scrape_userpics(self) -> dict:
        async def save(page, spinner, res):
            filename = f"{self.username} - Userpics"
            await self._save_page_assets(page, spinner, self.urls['userpics'], filename, res)

        return await self._scrape_task("userpics", "userpics", check_fn=check_for_userpics, save_fn=save)

    async def scrape_vgifts(self) -> dict:
        async def save(page, spinner, res):
            filename = f"{self.username} - Virtual Gifts"
            await self._save_page_assets(page, spinner, self.urls['vgifts'], filename, res)

        return await self._scrape_task("vgifts", "virtual gifts", check_fn=check_for_vgifts, save_fn=save)

    async def scrape_memories(self, mem_count = None) -> dict:
        async def check(page, mem_count) -> bool:
            if mem_count is None or mem_count == 0:
                return await check_for_memories(page)
            return True

        async def save(page, spinner, res):
            filename = f"{self.username} - Memories"
            await scroll_with_keyboard(page, spinner, mem_count)
            await page.wait_for_timeout(5000)
            await self._save_page_assets(page, spinner, self.urls['memories'], filename, res)

        return await self._scrape_task("memories", "memories", check_fn=check, save_fn=save)

    async def scrape_photos(self) -> dict:
        async def check(page, timeout) -> bool:
            if self.account_type != "personal":
                console.print(
                    f"    [bold][dim]ⓘ[/bold] Photo albums are not available for community accounts, skipping.[/dim]")
            return True if page else False

        async def save(page, spinner, res):
            filename = f"{self.username} - Photo Albums"
            await self._save_page_assets(page, spinner, self.urls['photos'], filename, res)

            # Extract album links
            from .photo_scraper import LiveJournalPhotoScraper
            
            # Find all links containing "/photo/album/"
            album_urls = []
            containers = await page.locator('[class^="CoversContainer-"]').all()
            for container in containers:
                href = await container.get_attribute("href")
                if href and href not in album_urls:
                    if href.startswith("//"):
                        href = f"https:{href}"
                    elif href.startswith("/"):
                        href = f"https://{self.username}.livejournal.com{href}"
                    album_urls.append(href)

            if not album_urls:
                console.print(f"    [bold yellow]⚠[/bold yellow] [dim]No photo albums found for {self.username}.[/dim]")
                return

            console.print(f"    [bold cyan]📷 Found {len(album_urls)} photo albums to download...[/bold cyan]")
            photo_scraper = LiveJournalPhotoScraper(self.context, headless=True, delay=self.delay)
            
            success_count = 0
            for idx, album_url in enumerate(album_urls):
                console.print(f"    [bold magenta]► Album {idx + 1}/{len(album_urls)}:[/bold magenta] {album_url}")
                parts = album_url.split("album/")
                album_id = parts[1].split("/")[0] if len(parts) > 1 else str(idx + 1)
                
                # Save inside username/photos/album_id
                album_dir = self.user_dir / "photos" / f"album_{album_id}"
                
                try:
                    ok = await photo_scraper.scrape_album(album_url, output_dir=album_dir)
                    if ok:
                        success_count += 1
                except Exception as e:
                    if "AuthenticationError" in type(e).__name__:
                        raise e
                    console.print(f"    [bold red]✗ Failed to download album {album_url}: {e}[/bold red]")

            res["success_count"] = success_count
            res["total_albums"] = len(album_urls)
            console.print(f"    [bold green]✓[/bold green] [dim]Downloaded {success_count}/{len(album_urls)} albums.[/dim]")

        return await self._scrape_task("photos", "photos", check_fn=check, save_fn=save)

    async def run_once_per_user(self, page: Page):
        """Custom hook executed once per user using the first successfully loaded page.
        Extracts user information from the page.
        """
        try:
            account_type = await get_account_type(page)
            if account_type is not None:
                if account_type == "personal":
                    self.account_type = "personal"
                    console.print(f"\n[bold magenta]:bust_in_silhouette:  Processing personal account:[/bold magenta] {self.username}")
                elif account_type == "community":
                    self.account_type = "community"
                    console.print(f"\n[bold magenta]:busts_in_silhouette:  Processing community account:[/bold magenta] {self.username}")

        except Exception as e:
            console.print(f"    [bold yellow]⚠[/bold yellow] [dim]Failed to extract initial user info:[/dim] {e}")

    async def process(self):
        """Executes all selected scraping tasks for the account."""
        output = Path("output")
        output.mkdir(exist_ok=True)
        self.user_dir.mkdir(exist_ok=True)

        if self.options.get("entries"):
            res = await self.scrape_entries()
            self.results["entries"] = "success" if res['success'] else "failed"

        if self.options.get("profile"):
            res = await self.scrape_profile()
            self.results["profile"] = "success" if res['success'] else "failed"
            self.results["mem_count"] = res.get("mem_count", 0)

        if self.options.get("tags"):
            res = await self.scrape_tags()
            self.results["tags"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

        if self.options.get("userpics"):
            res = await self.scrape_userpics()
            self.results["userpics"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

        if self.options.get("vgifts"):
            res = await self.scrape_vgifts()
            self.results["vgifts"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

        if self.options.get("memories"):
            mem_count_val = int(self.results.get("mem_count", 0)) if self.results["profile"] != "skipped" else 0
            res = await self.scrape_memories(mem_count_val)
            self.results["memories"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

        if self.options.get("photos"):
            res = await self.scrape_photos()
            self.results["photos"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

    async def retry_failed(self) -> bool:
        """Retries any tasks that failed during the initial pass."""
        improved = False
        self.is_retrying = True

        task_map = {
            "entries": self.scrape_entries,
            "profile": self.scrape_profile,
            "tags": self.scrape_tags,
            "userpics": self.scrape_userpics,
            "vgifts": self.scrape_vgifts,
            "memories": self.scrape_memories,
            "photos": self.scrape_photos
        }

        for task_name, task_method in task_map.items():
            if self.results.get(task_name) == "failed":
                console.print(f"    [bold yellow]↻ Retrying {task_name} for {self.username}...[/bold yellow]")
                
                if task_name == "memories":
                    mem_count_val = int(self.results.get("mem_count", 0)) if self.results["profile"] != "skipped" else 0
                    res = await self.scrape_memories(mem_count_val)
                else:
                    res = await task_method()

                if res['success']:
                    self.results[task_name] = "success"
                    improved = True
                    console.print(f"    [bold green]✓ Retry successful for {task_name}![/bold green]")
                else:
                    console.print(f"    [bold red]✗ Retry failed again for {task_name}.[/bold red]")

        self.is_retrying = False
        return improved
