from pathlib import Path

from playwright.async_api import Page

from .config import URL_SUFFIX, load_config, update_status
from .utils import (
    check_for_albums,
    check_for_memories,
    check_for_tags,
    check_for_userpics,
    check_for_vgifts,
    compress_pdf,
    download_html,
    download_pdf,
    get_account_type,
    get_logged_in,
    scroll_with_keyboard,
)

settings = load_config()

class LiveJournalAccount:
    """Represents a LiveJournal user and manages their specific scraping tasks."""
    has_checked_login = False
    logged_in_user = None

    def __init__(self, context, username: str, settings):
        self.context = context
        self.username = username
        self.account_type = None
        self.settings = settings
        self.format_options = settings.get("format_options")
        import random
        self.jitter = random.uniform(0.5, 1.5) * settings.get("delay", 3.0) # in seconds
        self.user_dir = Path(f"output/{username}")
        self.is_retrying = False
        self.timeout = settings.get("timeout", 30) if not self.is_retrying else settings.get("timeout", 30) * 2.25
        self.timeout_ms = int(self.timeout * 1000)
        self.status = None
        self.mem_count = None


        clean_username = username.replace("_", "-")
        self.base_url = f"https://users.livejournal.com/{clean_username}"
        self.urls = {
            "entries": self.base_url,
            "profile": f"{self.base_url}{URL_SUFFIX['profile']}",
            "tags": f"{self.base_url}{URL_SUFFIX['tags']}",
            "userpics": f"https://www.livejournal.com/allpics.bml?user={clean_username}",
            "vgifts": f"https://www.livejournal.com/manage/vgift.bml?u={clean_username}",
            "memories": f"{self.base_url}{URL_SUFFIX['memories']}",
            "memory_index": f"https://www.livejournal.com/tools/memories.bml?user={clean_username}",
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

    async def _fetch_page(self, url: str, max_attempts: int = 7) -> Page | None:
        """Navigates to the given URL with retries and returns the active page object."""
        import asyncio
        await asyncio.sleep(self.jitter)

        for attempt in range(max_attempts):
            try:
                update_status(f"Navigating to {url} (Attempt {attempt + 1}/{max_attempts})...")

                page = await self.context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.set_default_navigation_timeout(self.timeout_ms)
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
        update_status(f"Preparing to scrape {label}...")
        try:
            page = await self._fetch_page(url)
            update_status("Checking if page exists...")
            if check_fn and not await check_fn(page, int(self.jitter * 1000)):
                if task_name == "photos" and self.account_type == "community":
                    return result
                print(f"    [bold $text-warning]⚠[/bold $text-warning] [dim]No {label} found for {self.username}, skipping.[/dim]")
                return result

            if save_fn:
                await save_fn(page, result)
            result["success"] = True
        except Exception as e:
            if task_name != "photos" and self.account_type != "community":
                print(f"    [bold $error]✗[/bold $error] [dim]Failed:[/dim] {url} - {str(e)}")
                result["error"] = True
        finally:
            if page:
                await page.close()
        return result

    async def _save_page_assets(self, page, task_name, filename, res) -> None:
        """Helper to download both HTML and PDF, compress the PDF, and update results."""
        save_path = self.user_dir / filename

        # Determine what to save for this task
        task_option = self.format_options.get(task_name)
        if task_option is False or task_option is None:
            print(f"    [bold $warning]⚠[/bold $warning] [dim]Skipping saving assets for {task_name} (disabled).[/dim]")
            return

        save_html = task_option in ("html", "both")
        save_pdf = task_option in ("pdf", "both")

        if not save_html and not save_pdf:
            print(f"    [bold $warning]⚠[/bold $warning] [dim]Skipping saving assets for {task_name} (no formats enabled).[/dim]")
            return

        try:
            if save_html:
                update_status("[bold]Downloading HTML...[/bold]")
                await download_html(page, f"{save_path}.html")
                res["html"] = True

            if save_pdf:
                update_status("Downloading PDF...")
                if await download_pdf(page, f"{save_path}.pdf"):
                    res["pdf"] = True
                    update_status("[bold]Compressing PDF...[/bold]")
                    await compress_pdf(f"{save_path}.pdf")
        except Exception as e:
            print(f"    [bold red]✗[/bold red] [dim]Error saving assets for {task_name}:[/dim] {e}")

        if task_name == "memory_index":
            print(f"        [bold $success]✓[/bold $success] [dim]Saved assets for:[/dim] {task_name} (Memory Index only, not full memories)")
        if task_name != "photos" and task_name != "memory_index":
            print(f"    [bold $success]✓[/bold $success] [dim]Saved assets for:[/dim] {task_name}")

    async def scrape_entries(self) -> dict:
        async def save(page, res):
            title = await page.title()
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c in ' -_']).rstrip()
            safe_title = safe_title or f"{self.username} - Recent Entries"

            await self._save_page_assets(page, "entries", safe_title, res)

        return await self._scrape_task("entries", "recent entries", save_fn=save)

    async def scrape_profile(self) -> dict:
        async def save(page, res):
            filename = f"{self.username} - Profile"
            await self._save_page_assets(page, "profile", filename, res)
            
            memory_count = await page.locator('.b-profile-stat-memcount > .b-profile-stat-value').all_inner_texts()
            res["mem_count"] = memory_count[0].replace(',', '') if memory_count else "0"

        return await self._scrape_task("profile", "profile", save_fn=save)

    async def scrape_tags(self) -> dict:
        async def save(page, res):
            filename = f"{self.username} - Tags"
            await self._save_page_assets(page, "tags", filename, res)

        return await self._scrape_task("tags", "tags", check_fn=check_for_tags, save_fn=save)

    async def scrape_userpics(self) -> dict:
        async def save(page, res):
            filename = f"{self.username} - Userpics"
            await self._save_page_assets(page, "userpics", filename, res)

        return await self._scrape_task("userpics", "userpics", check_fn=check_for_userpics, save_fn=save)

    async def scrape_vgifts(self) -> dict:
        async def save(page, res):
            filename = f"{self.username} - Virtual Gifts"
            await self._save_page_assets(page, "vgifts", filename, res)

        return await self._scrape_task("vgifts", "virtual gifts", check_fn=check_for_vgifts, save_fn=save)

    async def scrape_memories(self) -> dict:
        async def check(page, res) -> bool:
            if self.mem_count is None:
                mems = await check_for_memories(page, timeout=(self.timeout * 1000))
                if mems:
                    print(
                        f"    [bold $warning]⚠[/bold $warning] [dim]Unknown memory count, a maximum of {settings.get('max_dl_memories', 750)} memories will be collected..."
                    )
                    return True
                else:
                    print(
                        f"    [bold $warning]⚠[/bold $warning] [dim]No memories found for {self.username}, skipping.[/dim]"
                    )
                    return False
            return True

        async def save(page, res):
            filename = f"{self.username} - Memories"
            await scroll_with_keyboard(page, self.mem_count if self.mem_count else settings.get('max_dl_memories', 500), settings.get('max_dl_memories', 500))
            await page.wait_for_timeout(5000)
            await self._save_page_assets(page, "memories", filename, res)

        if self.mem_count == 0:
            print(f"    [bold $warning]⚠[/bold $warning] [dim]No memories found for {self.username}, skipping.[/dim]")
            return {"success": False, "error": False}

        return await self._scrape_task("memories", "memories", check_fn=check, save_fn=save)

    async def scrape_mem_index(self):
        async def save(page, res):
            filename = f"{self.username} - Memory Index"
            await self._save_page_assets(page, "memory_index", filename, res)

        update_status("Navigating to Memory Index...")
        await self._scrape_task("memory_index", "memory index", save_fn=save)


    async def scrape_photos(self) -> dict:
        async def check(page, timeout) -> bool:
            if self.account_type != "personal":
                print("    [bold][dim]ⓘ[/bold] Photo albums are not available for community accounts, skipping.[/dim]")
                return False
            return await check_for_albums(page, timeout) if page else False

        async def save(page, res):
            filename = f"{self.username} - Photo Albums"
            await self._save_page_assets(page, "photos", filename, res)

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
                print(f"    [bold $warning]⚠[/bold $warning] [dim]No photo albums found for {self.username}.[/dim]")
                return

            photo_scraper = LiveJournalPhotoScraper(self.context, self.settings)

            success_count = 0
            for idx, album_url in enumerate(album_urls):
                print(f"    [bold $text-primary]► Album {idx + 1}/{len(album_urls)}:[/bold $text-primary] {album_url}")
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
                    print(f"    [bold red]✗ Failed to download album {album_url}: {e}[/bold red]")

            res["success_count"] = success_count
            res["total_albums"] = len(album_urls)
            print(f"    [bold $success]✓[/bold $success] [dim]Downloaded {success_count}/{len(album_urls)} albums.[/dim]")

        return await self._scrape_task("photos", "photos", check_fn=check, save_fn=save)

    async def run_once_per_user(self, page: Page):
        """Custom hook executed once per user using the first successfully loaded page.
        Extracts user information from the page.
        """
        try:
            if not LiveJournalAccount.has_checked_login:
                LiveJournalAccount.has_checked_login = True
                LiveJournalAccount.logged_in_user = await get_logged_in(page)
                if LiveJournalAccount.logged_in_user:
                    print(f"    [bold $success]✓[/bold $success] [dim]Logged in as {LiveJournalAccount.logged_in_user}[/dim]")
                else:
                    print("    [bold $warning]⚠[/bold $warning] [dim]Not logged in! Some content may be restricted.[/dim]")
            
            account_type = await get_account_type(page)
            if account_type is not None:
                if account_type == "personal":
                    self.account_type = "personal"
                    print(f"\n[bold $text-accent]👤  Processing personal account:[/bold $text-accent] {self.username}")
                elif account_type == "community":
                    self.account_type = "community"
                    print(f"\n[bold $text-accent]👥  Processing community account:[/bold $text-accent] {self.username}")

        except Exception as e:
            print(f"    [bold $warning]⚠[/bold $warning] [dim]Failed to extract initial user info:[/dim] {e}")

    async def process(self):
        """Executes all selected scraping tasks for the account."""
        output = Path("output")
        output.mkdir(exist_ok=True)
        self.user_dir.mkdir(exist_ok=True)

        if self.format_options.get("entries"):
            res = await self.scrape_entries()
            self.results["entries"] = "success" if res['success'] else "failed"

        if self.format_options.get("profile"):
            res = await self.scrape_profile()
            self.results["profile"] = "success" if res['success'] else "failed"
            self.results["mem_count"] = res.get("mem_count", "0")

        if self.format_options.get("tags"):
            res = await self.scrape_tags()
            self.results["tags"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

        if self.format_options.get("userpics"):
            res = await self.scrape_userpics()
            self.results["userpics"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

        if self.format_options.get("vgifts"):
            res = await self.scrape_vgifts()
            self.results["vgifts"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

        if self.format_options.get("memories"):
            self.mem_count = int(self.results.get("mem_count", 0)) if self.results["profile"] != "skipped" else 0
            res = await self.scrape_memories()
            self.results["memories"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

        if self.format_options.get("photos"):
            res = await self.scrape_photos()
            self.results["photos"] = "success" if res['success'] else "failed" if res['error'] else "skipped"

    async def retry_failed(self, status=None) -> bool:
        """Retries any tasks that failed during the initial pass."""
        if status:
            self.status = status
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
                print(f"    [bold $text-warning]↻ Retrying {task_name} for {self.username}...[/bold $text-warning]")
                
                if task_name == "memories":
                    self.mem_count = int(self.results.get("mem_count", 0)) if self.results["profile"] != "skipped" else 0
                    res = await self.scrape_memories()
                else:
                    res = await task_method()

                if res['success']:
                    self.results[task_name] = "success"
                    improved = True
                    print(f"    [bold $text-success]✓ Retry successful for {task_name}![/bold $text-success]")
                else:
                    print(f"    [bold $text-error]✗ Retry failed again for {task_name}.[/bold $text-error]")

        self.is_retrying = False
        return improved
