import builtins
import inspect
import os
import re
import sys
import asyncio

from playwright.async_api import async_playwright
from rich.spinner import Spinner
from rich.text import Text
import rich.rule
from textual import on, work

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll, Grid
from textual.content import Content
from textual.reactive import reactive
from textual.widgets import Header, Footer, Input, Button, Switch, Label, RichLog, Static, \
    DataTable, SelectionList, Rule, Collapsible, TabbedContent, TabPane

from textual_fspicker import FileOpen, Filters

import src.config
import rich.progress

from .save_posts import main_async
from .browser import run_login_flow, launch_browser_with_fallback
from .account_scraper import LiveJournalAccount
from .utils import parse_targets

os.environ["COLORTERM"] = "truecolor"

class SpinnerWidget(Static):
    """Basic spinner widget based on rich.spinner.Spinner."""
    def __init__(self, style: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spinner = Spinner(style)

    def on_mount(self) -> None:
        self.set_interval(1 / 10, self.refresh)

    def render(self):
        return self.spinner

class TextualStatus:
    def __init__(self, c, t, **k): self.c, self.t = c, t
    def __enter__(self): getattr(self.c, "update_status", lambda x: None)(self.t); return self
    def __exit__(self, *a): getattr(self.c, "update_status", lambda x: None)("")
    def update(self, t, **k): getattr(self.c, "update_status", lambda x: None)(t)

class LiveJournalScraperApp(App):
    # App configuration constants
    TITLE, SUBTITLE = "LiveJournal Scraper", "Backup your LiveJournal profile and photo albums"
    CSS_PATH = "tui.tcss"
    FORMAT_TASKS = ["entries", "profile", "tags", "userpics", "vgifts", "memories", "photos"]

    shared_user_data_dir = reactive("user_profile")
    shared_delay = reactive("0.0")
    shared_max_memories = reactive("750")
    shared_max_dl_memories = reactive("500")
    shared_timeout = reactive("30.0")

    def __init__(self, initial_settings=None, unknown_args=None, **kwargs):
        super().__init__(**kwargs)
        self.settings = initial_settings or {}
        self.unknown_args = unknown_args
        self._log_entries: list[str] = []

    def _invoke(self, f, *a, **k):
        # Textual UI updates must happen on the main thread.
        # call_from_thread handles this safely, with a fallback for older Textual versions
        # that throw RuntimeError when called directly from the main thread.
        try: self.call_from_thread(f, *a, **k)
        except RuntimeError: f(*a, **k)

    def _render_log(self, markup: str, **kwargs) -> str:
        rendered = markup
        for key, value in kwargs.items():
            value = re.sub(r"(?<!\\)\[", "\\\\[", str(value))
            value = re.sub(r"(?<!\\)]", "]", str(value))
            rendered = rendered.replace(f"${key}", str(value))
        return rendered

    def _write_log_markup(self, markup, **kwargs) -> None:
        self._log_entries.append(self._render_log(markup, **kwargs))
        log_view = self.query_one("#log-view", RichLog)
        if type(markup) is not str:
            log_view.write(markup, **kwargs)
        else:
            log_view.write(Content.from_markup(markup, **kwargs))

    def _clear_log(self) -> None:
        self._log_entries.clear()
        log_view = self.query_one("#log-view", RichLog)
        self._invoke(log_view.clear)

    def _replay_log(self) -> None:
        log_view = self.query_one("#log-view", RichLog)
        self._invoke(log_view.clear)
        if len(self._log_entries) > 0:
            for entry in self._log_entries:
                log_view.write(Content.from_markup(entry))

    def watch_theme(self, old_theme: str, new_theme: str) -> None:
        # forces it to wait for the stylesheet to update
        self.call_next(self._replay_log)

    def compose(self) -> ComposeResult:
        # compose() builds the widget tree once on startup to define the static layout hierarchy.
        yield Header()
        with Container(id="main-layout"):
            with TabbedContent(id="sidebar", initial="tab-extras"):
                with TabPane("Extras", id="tab-extras"):
                    with VerticalScroll():
                        with Grid(id="extras-target-input-container"):
                            yield Input(placeholder="e.g. news, target_list.txt", id="extras-target")
                            yield Button("🗀", id="btn-extras-files")
                        with Horizontal(id="extras-headless-container"):
                            yield Label("Run Headless Browser")
                            yield Rule(line_style="ascii")
                            yield Switch(value=True, id="extras-headless-switch")
                        with Horizontal(id="memory-max-container"):
                            yield Label("Max Memories to Scrape / Download")
                            yield Rule(line_style="ascii")
                            yield Input(placeholder="750", classes="integer", compact=True, type="integer",
                                        id="max-memories")
                            yield Label(" / ")
                            yield Input(placeholder="500", classes="integer", compact=True,
                                        type="integer", id="max-dl-memories")
                        with Vertical(id='format-selection-container'):
                            yield Label("FORMAT SELECTION", id='format-selection-label', classes="title")
                            with Horizontal(id="format-selection"):
                                with Vertical(id="html-col"):
                                    yield Label("DOWNLOAD HTML", classes="title html")
                                    yield SelectionList(*[(t.capitalize(), t, True) for t in self.FORMAT_TASKS],
                                                        id="html-selection")
                                with Vertical(id="pdf-col"):
                                    yield Label("DOWNLOAD PDF", classes="title pdf")
                                    yield SelectionList(*[(t.capitalize(), t, True) for t in self.FORMAT_TASKS],
                                                        id="pdf-selection")

                        with Collapsible(title="Advanced Options", id="extras-adv-options", collapsed=True):
                            with Vertical(id="extras-adv-options-contents"):
                                with Horizontal(id="extras-user-data-dir-container"):
                                    yield Label("User Data Directory", id="extras-user-data-dir-label")
                                    yield Rule(line_style="ascii")
                                    yield Input(id="extras-user-data-dir", compact=True, value=self.shared_user_data_dir)
                                with Horizontal(id="extras-delay-max-container"):
                                    yield Label("Delay Between Requests (seconds)", id="extras-delay-max-label")
                                    yield Rule(line_style="ascii")
                                    yield Input(classes="number", compact=True, type="number", id="delay", value=self.shared_delay)

                                with Horizontal(id="extras-timeout-container"):
                                    yield Label("Request Timeout (seconds)", id="extras-timeout-label")
                                    yield Rule(line_style="ascii")
                                    yield Input(classes="number", compact=True, type="number", id="timeout",
                                                value=self.shared_timeout)


                with TabPane("Posts", id="tab-posts"):
                    with VerticalScroll():
                        with Container(id="posts-target-input-container"):
                            yield Input(placeholder="e.g. news, target_list.txt", id="posts-target")
                            yield Button("🗀", classes="open-file-picker", id="btn-posts-files")
                        with Horizontal(id="headless-container"):
                            yield Label("Run Headless Browser")
                            yield Rule(line_style="ascii")
                            yield Switch(value=True, id="posts-headless-switch")

                        with Collapsible(title="Advanced Options", id="posts-adv-options"):
                            with Vertical(id="posts-adv-options-contents"):
                                with Horizontal(id="posts-user-data-dir-container"):
                                    yield Label("User Data Directory", id="posts-user-data-dir-label")
                                    yield Rule(line_style="ascii")
                                    yield Input(id="posts-user-data-dir", compact=True, value=self.shared_user_data_dir)
                                with Horizontal(id="posts-delay-max-container"):
                                    yield Label("Delay Between Requests (seconds)", id="posts-delay-max-label")
                                    yield Rule(line_style="ascii")
                                    yield Input(classes="number", compact=True, type="number", id="delay",
                                                value=self.shared_delay)
                                with Horizontal(id="posts-timeout-container"):
                                    yield Label("Request Timeout (seconds)", id="posts-timeout-label")
                                    yield Rule(line_style="ascii")
                                    yield Input(classes="number", compact=True, type="number", id="timeout",
                                                value=self.shared_timeout)
            with Vertical(id="log-container"):
                yield Label("EXECUTION LOGS", classes="title")
                yield RichLog(highlight=False, markup=True, id="log-view")
                yield DataTable(id="results-table", cursor_type="row", zebra_stripes=True, show_header=True, show_cursor=True, show_row_labels=False)

        with Horizontal(id="status-panel"):
            with Horizontal(id="progress-container"):
                with SpinnerWidget(style="dots", id="progress-spinner") as sw:
                    sw.visible = False

                yield Label("[b $text-primary]Status:[/b $text-primary] Ready", id="status-label")

        with Horizontal(id="buttons-row"):
            yield Button("Start Scraping Extras", variant="success", id="btn-extras")
            yield Button("Start Scraping Posts", variant="success", id="btn-posts")
            yield Button("Run Login Flow", variant="primary", id="btn-login")
            yield Button("Install Linux Deps", variant="warning", id="btn-deps")
            yield Button("Quit", variant="error", id="btn-quit")

        yield Footer()

    def on_mount(self) -> None:
        # on_mount() runs after the DOM is ready.
        sidebar = self.query_one("#sidebar")
        sidebar.border_title = "SCRAPER SETTINGS"

        # override the default print and force it to use textual's colors
        def new_print(*args, **kwargs):
            self._write_log_markup(*args, **kwargs)
        builtins.print = new_print
        src.config.console.status = lambda text, spinner="dots": TextualStatus(
            src.config.console, text
        )
        src.config.console.update_status = lambda text: self._invoke(
            self.set_status, text
        )
        # Repopulate the inputs and selections from the saved initial_settings dict.
        s = self.settings
        self.query_one("#extras-target", Input).value = s.get("target", "")
        self.query_one("#extras-target-input-container", Grid).border_title = "Target (Username, URL, or .txt file)"
        self.query_one("#extras-headless-switch", Switch).value = s.get("headless", True)
        self.query_one("#btn-posts", Button).styles.display = "none"
        self.query_one('#max-memories', Input).value = str(s.get("max_memories", "750"))
        self.query_one('#max-dl-memories', Input).value = str(s.get("max_dl_memories", "500"))
        self.query_one('#posts-target-input-container', Container).border_title = "Target (Single Post URL, .xlsm, or .txt file)"
        self.shared_delay = str(s.get("delay", "0.0"))
        self.shared_user_data_dir = s.get("user_data_dir", "user_profile")
        self.shared_timeout = str(s.get("timeout", "30.0"))
        self.shared_max_memories = str(s.get("max_memories", "750"))
        self.shared_max_dl_memories = str(s.get("max_dl_memories", "500"))

        try:
            html_list = self.query_one("#html-selection", SelectionList)
            pdf_list = self.query_one("#pdf-selection", SelectionList)
            for task in self.FORMAT_TASKS:
                val = s.get(task)
                if val not in (["both"], ["html"], ["pdf"], ["none"]):
                    print(f"[bold $text-warning]Warning: Invalid value for {task}: {val}. Defaulting to ['both'].[/bold $text-warning]")
                    val = ["both"]

                if val in (["both"], ["html"]):
                    html_list.select(task)
                else:
                    html_list.deselect(task)

                if val in (["both"], ["pdf"]):
                    pdf_list.select(task)
                else:
                    pdf_list.deselect(task)
        except Exception:
            pass

        tasks = ["entries", "profile", "tags", "userpics", "vgifts", "memories", "photos"]
        cli_flags = [f"--{task}" for task in tasks]
        if any(flag in sys.argv for flag in cli_flags):
            print("[$text-warning]Notice: Command line arguments overrode config.json tasks.[/$text-warning]\n")

        table = self.query_one("#results-table", DataTable)
        table.add_columns("Status", "Account", "Entries", "Profile", "Tags", "Userpics", "Virtual Gifts", "Memories", "Photos")

    def on_ready(self) -> None:
        # on_ready() runs after the app is fully initialized and ready to accept user input.
        if self.unknown_args:
            print(
                "\n[$text-warning][b]Warning:[/b] Unknown arguments in arguments or config file: $unknown_args [/$text-warning]",
                unknown_args=self.unknown_args
            )
        else:
            print('')
        self.query_one("#extras-target", Input).focus()

    @on(TabbedContent.TabActivated, pane='#tab-extras')
    def display_extras_button(self) -> None:
        posts_btn = self.query_one("#btn-posts", Button)
        extras_btn = self.query_one("#btn-extras", Button)
        posts_btn.styles.display = "none"
        extras_btn.styles.display = "block"
        extras_target = self.query_one("#extras-target", Input).value
        posts_target = self.query_one("#posts-target", Input).value
        if not extras_target and posts_target:
            self.query_one("#extras-target", Input).value = posts_target


    @on(TabbedContent.TabActivated, pane='#tab-posts')
    def display_posts_button(self) -> None:
        posts_btn = self.query_one("#btn-posts", Button)
        extras_btn = self.query_one("#btn-extras", Button)
        extras_btn.styles.display = "none"
        posts_btn.styles.display = "block"
        posts_target = self.query_one("#posts-target", Input).value
        extras_target = self.query_one("#extras-target", Input).value
        if not posts_target and extras_target:
            self.query_one("#posts-target", Input).value = extras_target

    @on(Input.Changed, "#extras-user-data-dir, #posts-user-data-dir")
    def update_shared_user_data_dir(self, event: Input.Changed):
        self.shared_user_data_dir = event.value
        if event.input.id == "extras-user-data-dir":
            self.query_one("#posts-user-data-dir", Input).value = event.value
        elif event.input.id == "posts-user-data-dir":
            self.query_one("#extras-user-data-dir", Input).value = event.value

    def set_status(self, text: str):
        self.query_one("#status-label", Label).update(f"[b $text-primary]Status:[/b $text-primary] {text}" if text else "[b $text-primary]Status:[/b $text-primary] Ready")

    def toggle_controls(self, disabled: bool):
        self.query("#sidebar, Button").exclude("#btn-quit").set(disabled=disabled)

    def start_scraping(self):
        # Core scraping flow.
        # 1. Gather all inputs from the UI.
        # 2. Reconstruct the `options` dictionary expected by the backend.
        # 3. Launch Playwright context and process each target sequentially.
        self.query("#sidebar, Button").exclude("#btn-quit").set(disabled=True)
        self.query_one("#progress-spinner", SpinnerWidget).visible = True
        self.set_status("")

    def on_scraping_finished(self):
        self.query("#sidebar, Button").set(disabled=False)
        self.query_one("#progress-spinner", SpinnerWidget).visible = False
        self.set_status("")

    @work
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        # Event handler for all Button presses, routed by button id.
        actions = {
            "btn-quit": self.exit,
            "btn-extras": self.start_extras_scraping_flow,
            "btn-posts": self.start_posts_scraping_flow,
            "btn-login": self.start_login_flow,
            "btn-deps": self.start_deps_flow,
            "btn-extras-files": self.open_file_picker,
            "btn-posts-files": self.open_file_picker
        }
        action = actions.get(event.button.id)
        if action and inspect.iscoroutinefunction(action):
            await action(event.button.id)
        elif action:
            action()

    def _start_worker(self, coro, name: str):
        if hasattr(self, "_active_worker") and self._active_worker.is_running:
            return
        self.start_scraping()
        self._active_worker = self.run_worker(coro(), name=name)

    def start_extras_scraping_flow(self):
        self._start_worker(self.run_extras_scraper_async, "scraper")

    def start_posts_scraping_flow(self):
        self._start_worker(self.run_posts_scraper_async, "scraper")

    def start_login_flow(self):
        self._start_worker(self.run_login_async, "login")

    def start_deps_flow(self):
        self._start_worker(self.run_deps_async, "deps")

    async def open_file_picker(self, btn_id):
        if opened := await self.push_screen_wait(FileOpen(title="Select Target File", filters=Filters(
                ("Text Files", lambda p: p.suffix.lower() == ".txt"),
                ("All Files", lambda p: True)
        ))):
            if btn_id == "btn-extras-files":
                self.query_one("#extras-target", Input).value = str(opened)
            elif btn_id == "btn-posts-files":
                self.query_one("#posts-target", Input).value = str(opened)

    async def run_extras_scraper_async(self):
        # Reset the once-per-run login check flag
        LiveJournalAccount.has_checked_login = False
        log = self.query_one("#log-view", RichLog)
        self.query_one("#results-table", DataTable).display = False
        self.set_status("Starting scraping...")

        # can't be print() b/c custom print doesn't accept "expand".
        ruler = rich.rule.Rule(title="\n[bold]Scraping Extras[/bold]", style="$text-accent")
        log.write(ruler, expand=True)

        target = self.query_one("#extras-target", Input).value.strip()
        if not target:
            print("[$text-error][b]Error:[/b] Target is required![/$text-error]")
            return self.on_scraping_finished()

        user_data_dir = self.query_one("#extras-user-data-dir", Input).value.strip() or "user_profile"
        os.environ["USER_DATA_DIR"] = user_data_dir

        def parse_num(field_id, default, num_type):
            try:
                return num_type(self.query_one(field_id, Input).value.strip())
            except ValueError:
                return default

        delay = parse_num("#delay", 0.0, float)
        headless = self.query_one("#extras-headless-switch", Switch).value

        html_tasks = self.query_one("#html-"
                                    "selection", SelectionList).selected
        pdf_tasks = self.query_one("#pdf-selection", SelectionList).selected
        format_options = {}
        for task in self.FORMAT_TASKS:
            is_html = task in html_tasks
            is_pdf = task in pdf_tasks
            format_options[task] = "both" if is_html and is_pdf else "html" if is_html else "pdf" if is_pdf else False

        settings = {
            "user_data_dir": user_data_dir,
            "delay": parse_num("#delay", 3.0, float),
            "max_memories": parse_num("#max-memories", 750, int),
            "max_dl_memories": parse_num("#max-dl-memories", 500, int),
            "headless": self.query_one("#extras-headless-switch", Switch).value,
            "format_options": format_options
        }

        profile_targets, album_targets = parse_targets(target)
        if not profile_targets and not album_targets:
            print("[$text-warning][b]Error: [/b]Invalid target. Provide URL, username, or .txt file.[/$text-warning]")
            self.on_scraping_finished()

        start_time = asyncio.get_event_loop().time()
        all_results = []

        try:
            async with async_playwright() as p:
                if len(profile_targets) > 1:
                    print(f"[$text-secondary]Preparing to scrape {len(profile_targets)} LJ accounts...[/$text-secondary]\n")
                elif len(profile_targets) == 1:
                    print(f"[$text-secondary]Preparing to scrape LJ account:[/$text-secondary][$text-accent] {profile_targets[0]}[/$text-accent]\n")
                self.set_status("[$text-primary]Launching browser context...[/$text-primary]")
                context = await launch_browser_with_fallback(
                    p, user_data_dir=user_data_dir, headless=self.query_one("#extras-headless-switch", Switch).value,
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )

                try:
                    for username in profile_targets:
                        self.set_status(f"Processing LJ account: {username}")
                        lj_user = LiveJournalAccount(context, username, options)
                        await lj_user.process()
                        all_results.append(lj_user)

                    failed_users = [u for u in all_results if "failed" in u.results.values()]
                    if failed_users:
                        print("\n[bold $text-warning]=== Retrying Failed Tasks ===[/bold $text-warning]\n")
                        for user in failed_users:
                            self.set_status(f"Retrying: {user.username}")
                            await user.retry_failed(status=None)

                    if album_targets:
                        photo_scraper = LiveJournalPhotoScraper(context, headless=headless, delay=delay, status=None)
                        for idx, album_url in enumerate(album_targets):
                            self.set_status(f"Processing Album {idx + 1}/{len(album_targets)}")
                            await photo_scraper.scrape_album(album_url)

                finally:
                    await context.close()

            elapsed = asyncio.get_event_loop().time() - start_time
            if all_results:
                self.populate_results_table(all_results, elapsed)
            else:
                print(f"\n[$text-success]Done! Total elapsed time: {elapsed:.1f}s[/$text-success]\n")

        except Exception as e:
            if "AuthenticationError" in type(e).__name__:
                print(f"\n[bold $text-error]❌ Authentication Error: {e}[/bold $text-error]\nRun login flow first.\n")
            else:
                print(f"\n[bold $text-error]Error: {e}[/bold $text-error]\n")
                import traceback
                print(traceback.format_exc())
        finally:
            self.on_scraping_finished()

    async def run_posts_scraper_async(self):
        # Similar to run_extras_scraper_async, but for posts. Implementation would go here.
        # Reset the once-per-run login check flag
        LiveJournalAccount.has_checked_login = False

        self.query_one("#results-table", DataTable).display = False
        self.set_status("Starting scraping...")

        target = self.query_one("#posts-target", Input).value.strip()
        if not target:
            print("[bold $text-error]Error: Target is required![/bold $text-error]")
            self.on_scraping_finished()

        user_data_dir = self.query_one("#posts-user-data-dir", Input).value.strip() or "user_profile"
        os.environ["USER_DATA_DIR"] = user_data_dir

        start_time = asyncio.get_event_loop().time()
        all_results = []

        try:
            await main_async(target, settings=self.settings)
        except Exception as e:
            print(f"\n[bold red]Error: {e}[/bold red]\n")
            import traceback
            print(traceback.format_exc())
        finally:
            elapsed = asyncio.get_event_loop().time() - start_time
            print(f"\n[bold $text-success]Done! Total elapsed time: {elapsed:.1f}s[/bold $text-success]\n")
            self.on_scraping_finished()


    async def run_login_async(self):
        self._clear_log()
        self.set_status("Running Login Flow...")
        try:
            await run_login_flow(self.query_one("#extras-user-data-dir", Input).value.strip() or "user_profile")
        except Exception as e:
            print(f"[bold red]Login flow failed: {e}[/bold red]\n")
        finally:
            self.on_scraping_finished()

    def populate_results_table(self, all_users, elapsed_time):
        table = self.query_one("#results-table", DataTable)
        table.clear()

        def format_icon(status: str) -> Text:
            if status == "success":
                return Text("✓", style="$text-success")
            elif status == "failed":
                return Text("✗", style="$text-error")
            return Text("-", style="dim")

        for user in all_users:
            has_failures = "failed" in user.results.values()
            status = Text("✗ Failed", style="$text-error") if has_failures else Text("✓ Success", style="$text-success")
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
        table.display = True
        print(f"\n[bold $text-success]Done! Total elapsed time: {elapsed_time:.1f}s[/bold $text-success]\n")

    async def run_deps_async(self):
        log = self.query_one("#log-view", RichLog)
        log.clear()
        self.set_status("Installing dependencies...")
        print("[bold $text-primary]Installing Playwright Linux dependencies...[/bold $text-primary]\n")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install-deps",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                print(f"[bold red]Failed (exit {proc.returncode}):[/bold red]\n{stderr.decode('utf-8', 'replace')}")
            else:
                print("[bold $text-success]Dependencies installed successfully![/bold $text-success]\n")
        except Exception as e:
            print(f"[bold red]Error: {e}[/bold red]\n")
        finally:
            self.on_scraping_finished()