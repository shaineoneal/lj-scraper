import asyncio
import random
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
    TASKS = ("entries", "profile", "tags", "userpics", "vgifts", "memories", "photos")
    has_checked_login = False
    logged_in_user = None

    def __init__(self, context, username: str, settings):
        self.context = context
        self.username = username
        self.account_type = None
        self.settings = settings
        self.format_options = settings.get("format_options")
        self.jitter = random.uniform(0.5, 1.5) * settings.get("delay", 3.0)  # seconds
        self.user_dir = Path(f"output/{username}")
        self.timeout = settings.get("timeout", 30)
        self.timeout_ms = int(self.timeout * 1000)
        self.status = None
        self.acct_mem_count = None

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

        self.results = {"username": self.username, **dict.fromkeys(self.TASKS, "skipped")}
        self.has_run_user_info = False

    async def _fetch_page(self, url: str, max_attempts: int = 7) -> Page | None:
        """Navigates to the given URL with retries and returns the active page object."""
        await asyncio.sleep(self.jitter)

        for attempt in range(max_attempts):
            page = None
            retry = True
            try:
                update_status(f"Navigating to {url} (Attempt {attempt + 1}/{max_attempts})...")

                page = await self.context.new_page()
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)

                if resp and resp.status != 200:
                    if resp.status == 404:
                        if 'photo' in url:
                            await page.close()
                            return None
                        retry = False  # 404 on a normal page is final
                    raise Exception(f"HTTP Status {resp.status}", resp.status)

                if not self.has_run_user_info:
                    self.has_run_user_info = True
                    await self.run_once_per_user(page)
                    if self.account_type == "community" and 'photo' in url:
                        retry = False
                        raise Exception("Community account detected, skipping photo albums.")
                return page
            except Exception as e:
                if page:
                    await page.close()
                if not retry or attempt == max_attempts - 1:
                    raise
        return None

    async def _scrape_task(self, task_name: str, label: str, check_fn=None, save_fn=None) -> dict:
        """Generic task runner that standardizes the fetch/check/save lifecycle."""
        result = {"html": False, "pdf": False, "success": False, "error": None}
        url = self.urls[task_name]
        page = None
        update_status(f"Preparing to scrape {label}...")

        if task_name == "photos" and self.account_type == "community":
            print(f"    [bold][dim]ⓘ[/bold] Photo albums are not available for community accounts, skipping.[/dim]")
            return result
        
        try:
            page = await self._fetch_page(url)
            update_status(f"Checking if {label.capitalize()} page exists...")
            if check_fn and not await check_fn(page, self.timeout_ms):
                print(f"    [bold $warning]⚠[/bold $warning] [dim]No {label} found for {self.username}, skipping.[/dim]")
                return result

            if save_fn:
                await save_fn(page, result)
            result["success"] = True
        except Exception as e:
            if task_name != "photos":
                print(f"    [bold $error]✗[/bold $error] [dim]Failed:[/dim] {url} - {e}")
                result["error"] = True
        finally:
            if page:
                await page.close()
        return result

    async def _save_page_assets(self, page, task_name: str, filename, res) -> None:
        """Helper to download both HTML and PDF, compress the PDF, and update results."""
        save_path = self.user_dir / filename

        # The memory index follows the memories format setting.
        task_option = self.format_options.get("memories" if task_name == "memory_index" else task_name)
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
            raise

        if task_name != "photos":
            print(f"    [bold $success]✓[/bold $success] [dim]Saved assets for:[/dim] {task_name}")

    async def scrape_entries(self) -> dict:
        async def save(page, res):
            await page.wait_for_load_state("load")
            title = await page.title()
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c in ' -_']).rstrip()
            safe_title = safe_title or f"{self.username} - Recent Entries"

            await self._save_page_assets(page, "entries", safe_title, res)

        return await self._scrape_task("entries", "recent entries", save_fn=save)

    async def scrape_profile(self) -> dict:
        async def save(page, res):
            filename = f"{self.username} - Profile"
            await page.wait_for_load_state("load")
            stat_loc = page.locator('.b-profile-stat-memcount > .b-profile-stat-value').first
            if await stat_loc.count() > 0:
                memory_count = await stat_loc.text_content()
                self.acct_mem_count = int(memory_count.replace(',', '')) if memory_count else 0
            else:
                self.acct_mem_count = 0

            await self._save_page_assets(page, "profile", filename, res)

        return await self._scrape_task("profile", "profile", save_fn=save)

    async def _scrape_asset_page(self, task_name: str, label: str, filename: str, check_fn=None) -> dict:
        """Fetch one page and save its HTML/PDF assets."""
        async def save(page, res):
            await page.wait_for_load_state("load")
            await self._save_page_assets(page, task_name, filename, res)

        return await self._scrape_task(task_name, label, check_fn=check_fn, save_fn=save)

    async def scrape_tags(self) -> dict:
        return await self._scrape_asset_page("tags", "tags", f"{self.username} - Tags", check_for_tags)

    async def scrape_userpics(self) -> dict:
        return await self._scrape_asset_page("userpics", "userpics", f"{self.username} - Userpics", check_for_userpics)

    async def scrape_vgifts(self) -> dict:
        return await self._scrape_asset_page("vgifts", "virtual gifts", f"{self.username} - Virtual Gifts", check_for_vgifts)

    async def scrape_mem_index(self):
        update_status("Navigating to Memory Index...")
        await self._scrape_asset_page("memory_index", "memory index", f"{self.username} - Memory Index")

    async def scrape_memories(self) -> dict:
        async def check(page, timeout) -> bool:
            if self.acct_mem_count == 0 or self.acct_mem_count is None:
                mems = await check_for_memories(page, timeout=timeout)
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
            max_dl = int(settings.get('max_dl_memories', 500))
            if self.acct_mem_count and self.acct_mem_count > max_dl:
                print(
                    f"    [bold $warning]⚠[/bold $warning] [dim]Memory count ({self.acct_mem_count}) exceeds max_memories, collecting the index and the first {max_dl} memories..."
                )
                await self.scrape_mem_index()

            await page.wait_for_load_state("load")
            scroll_count = self.acct_mem_count if self.acct_mem_count and self.acct_mem_count < max_dl else max_dl
            await scroll_with_keyboard(page, scroll_count)
            await page.wait_for_timeout(5000)
            await self._save_page_assets(page, "memories", filename, res)

        return await self._scrape_task("memories", "memories", check_fn=check, save_fn=save)

    async def scrape_photos(self) -> dict:
        async def check(page, timeout) -> bool:
            if self.account_type != "personal":
                print("    [bold][dim]ⓘ[/bold] Photo albums are not available for community accounts, skipping.[/dim]")
                return False
            return await check_for_albums(page, timeout) if page else False

        async def save(page, res):
            filename = f"{self.username} - Photo Albums"
            await page.wait_for_load_state("load")
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
            
            icons = {"personal": "👤", "community": "👥"}
            account_type = await get_account_type(page)
            if account_type in icons:
                self.account_type = account_type
                print(
                    f"\n[bold $text-accent]{icons[account_type]}  "
                    f"[{self.settings.get('index')}/{self.settings.get('total')}] Processing {account_type} account:"
                    f"[/bold $text-accent] {self.username}"
                )

        except Exception as e:
            print(f"    [bold $warning]⚠[/bold $warning] [dim]Failed to extract initial user info:[/dim] {e}")

    def _record_result(self, task_name: str, res: dict) -> None:
        """Map a task result to the status strings the UI and retry pass expect."""
        # entries/profile have never reported "skipped"; keep that contract.
        failed = res.get("error") or task_name in ("entries", "profile")
        self.results[task_name] = "success" if res["success"] else "failed" if failed else "skipped"

    async def process(self):
        """Executes all selected scraping tasks for the account."""
        Path("output").mkdir(exist_ok=True)
        self.user_dir.mkdir(exist_ok=True)

        for task_name in self.TASKS:
            if self.format_options.get(task_name):
                res = await getattr(self, f"scrape_{task_name}")()
                self._record_result(task_name, res)

    async def retry_failed(self, status=None) -> bool:
        """Retries any tasks that failed during the initial pass."""
        if status:
            self.status = status
        improved = False

        for task_name in self.TASKS:
            if self.results.get(task_name) != "failed":
                continue
            print(f"    [bold $text-warning]↻ Retrying {task_name} for {self.username}...[/bold $text-warning]")
            res = await getattr(self, f"scrape_{task_name}")()
            if res["success"]:
                self.results[task_name] = "success"
                improved = True
                print(f"    [bold $text-success]✓ Retry successful for {task_name}![/bold $text-success]")
            else:
                print(f"    [bold $text-error]✗ Retry failed again for {task_name}.[/bold $text-error]")

        return improved
