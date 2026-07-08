import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import configargparse
from playwright.async_api import async_playwright
from rich.panel import Panel

from .config import console, DEFAULT_USER_DATA_DIR, settings
from .browser import run_login_flow, launch_browser_with_fallback
from .account_scraper import LiveJournalAccount
from .photo_scraper import LiveJournalPhotoScraper
from .utils import parse_targets, print_summary_table


def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

class JSONConfigFileParser(configargparse.ConfigFileParser):
    def get_syntax_description(self):
        return "JSON"

    def parse(self, stream):
        try:
            data = json.load(stream)
        except Exception as e:
            raise ValueError(f"Could not parse JSON config file: {e}")
        
        result = {}
        for k, v in data.items():
            if v is None:
                continue
            normalized_key = k.replace('_', '-')
            if isinstance(v, list):
                result[normalized_key] = [str(x) for x in v]
            else:
                result[normalized_key] = str(v)
        return result


async def main_async():
    parser = configargparse.ArgumentParser(
        description="Scrape and download LiveJournal accounts and photo albums.",
        default_config_files=["config.json"],
        config_file_parser_class=JSONConfigFileParser
    )
    parser.add_argument("target", nargs="?", default=None,
                        help="A LiveJournal profile URL, username, photo album URL, or .txt file containing them.")
    parser.add_argument("--target", dest="target_opt", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--config", is_config_file=True,
                        help="Path to a JSON config file to load settings from (default: config.json).")
    parser.add_argument("--user-data-dir", default=None,
                        help=f"Directory for browser session data (default: read from config or USER_DATA_DIR env var or '{DEFAULT_USER_DATA_DIR}')")
    parser.add_argument("--login", type=str2bool, nargs="?", const=True, default=None,
                        help="Launch browser to log in manually and save session credentials.")
    parser.add_argument("--headed", type=str2bool, nargs="?", const=True, default=None,
                        help="Run browser in headed mode with a visible window.")
    parser.add_argument("--headless", action="store_true", default=None, help="Run browser in headless mode.")
    parser.add_argument("--delay", type=float, default=None,
                        help="Time in seconds to wait before page actions/downloads.")
    parser.add_argument("--install-deps", type=str2bool, nargs="?", const=True, default=None,
                        help="Install missing Linux system dependencies for Playwright.")

    # Selective account scraping flags
    parser.add_argument("--entries", type=str2bool, nargs="?", const=True, default=None, help="Scrape recent entries.")
    parser.add_argument("--profile", type=str2bool, nargs="?", const=True, default=None, help="Scrape user profile.")
    parser.add_argument("--tags", type=str2bool, nargs="?", const=True, default=None, help="Scrape tags.")
    parser.add_argument("--userpics", type=str2bool, nargs="?", const=True, default=None, help="Scrape userpics.")
    parser.add_argument("--vgifts", type=str2bool, nargs="?", const=True, default=None, help="Scrape virtual gifts.")
    parser.add_argument("--memories", type=str2bool, nargs="?", const=True, default=None, help="Scrape memories.")
    parser.add_argument("--photos", type=str2bool, nargs="?", const=True, default=None,
                        help="Scrape photos (downloads metadata and photo albums).")

    parser.add_argument("--max-memories", type=int, nargs="?", const=True, default=750,
                        help="Maximum number of memories to scrape (default: 750).")
    parser.add_argument("--max-dl-memories", type=int, nargs="?", const=True, default=500,
                        help="Maximum number of memories to download (default: 500).")

    args = parser.parse_args()

    # Sync parsed args back into config.settings
    from . import config
    config.settings.update({k: v for k, v in vars(args).items() if v is not None})

    # Resolve target
    target = args.target or args.target_opt
    if target:
        config.settings["target"] = target

    # Resolve settings / args
    install_deps = config.settings.get("install_deps", False)
    if install_deps:
        console.print("[bold blue]Installing missing Linux system dependencies for Playwright...[/bold blue]")
        import playwright.__main__
        old_argv = sys.argv
        sys.argv = ["playwright", "install-deps"]
        try:
            playwright.__main__.main()
        except SystemExit as e:
            if e.code != 0:
                console.print(f"[bold red]Failed to install dependencies (exit code {e.code}).[/bold red]")
                sys.exit(e.code)
        finally:
            sys.argv = old_argv
        console.print("[bold green]System dependencies installation complete![/bold green]")
        return

    # Determine user data directory
    user_data_dir = config.settings.get("user_data_dir") or os.environ.get("USER_DATA_DIR") or "user_profile"
    os.environ["USER_DATA_DIR"] = user_data_dir

    login = config.settings.get("login", False)
    if login:
        await run_login_flow(user_data_dir)
        return

    target = config.settings.get("target")
    if not target:
        parser.print_help()
        return

    # Parse targets into profile usernames and specific album URLs
    profile_targets, album_targets = parse_targets(target)

    if not profile_targets and not album_targets:
        console.print("[bold red]Please provide a valid LiveJournal profile URL, username, album URL, or text file of targets as an argument.[ / bold red]")
        sys.exit(1)

    # Profile scraping options: respect selective flags if any are set on command line
    cli_flags = ["--entries", "--profile", "--tags", "--userpics", "--vgifts", "--memories", "--photos"]
    any_cli_flag = any(flag in sys.argv for flag in cli_flags)
    if any_cli_flag:
        options = {
            "entries": "--entries" in sys.argv,
            "profile": "--profile" in sys.argv,
            "tags": "--tags" in sys.argv,
            "userpics": "--userpics" in sys.argv,
            "vgifts": "--vgifts" in sys.argv,
            "memories": "--memories" in sys.argv,
            "photos": "--photos" in sys.argv
        }
    else:
        options = {
            "entries": config.settings.get("entries", True),
            "profile": config.settings.get("profile", True),
            "tags": config.settings.get("tags", True),
            "userpics": config.settings.get("userpics", True),
            "vgifts": config.settings.get("vgifts", True),
            "memories": config.settings.get("memories", True),
            "photos": config.settings.get("photos", True)
        }

    console.print(f"[bold blue]Scraping options:[/bold blue] {options}")
    start_time = time.time()
    all_results = []

    # Resolve headless setting: CLI flags override config setting
    headed = args.headed
    if "--headless" in sys.argv or args.headless:
        headed = False

    if headed is None:
        headed = False

    headless = not headed

    # Resolve delay setting
    delay = config.settings.get("delay")
    if delay is None:
        delay = config.settings.get("default_delay", 0.0)

    async with async_playwright() as p:
        console.print(
            f"[bold blue]Launching browser context from session directory:[/bold blue] [dim]{Path(user_data_dir).resolve()}[/dim]\n")
        context = await launch_browser_with_fallback(
            p,
            user_data_dir=user_data_dir,
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        try:
            # 1. Process profile scraping targets
            if profile_targets:
                with console.status("[bold blue]Scraping LJ accounts...[/bold blue]") as status:
                    for username in profile_targets:
                        status.update(f"[bold magenta]► Processing LJ account: {username}[/bold magenta]")
                        lj_user = LiveJournalAccount(context, username, options, delay=delay, status=status)
                        await lj_user.process()
                        all_results.append(lj_user)

                    # Retry pass for failed profile tasks
                failed_users = [user for user in all_results if "failed" in user.results.values()]
                if failed_users:
                    console.print("\n[bold yellow]=== Initiating Retry Pass for Failed Tasks ===[/bold yellow]")
                    for user in failed_users:
                        console.print(f"\n[bold magenta]► Retrying:[/bold magenta] {user.username}")
                        await user.retry_failed()

            # 2. Process standalone album targets
            if album_targets:
                console.print(
                    f"\n[bold green]=== Starting photo album downloads for {len(album_targets)} album(s) ===[/bold green]")
                photo_scraper = LiveJournalPhotoScraper(context, headless=headless, delay=delay)
                success_count = 0
                for idx, album_url in enumerate(album_targets):
                    console.print(
                        f"\n[bold magenta]► Processing Album {idx + 1}/{len(album_targets)}:[/bold magenta] {album_url}")
                    ok = await photo_scraper.scrape_album(album_url)
                    if ok:
                        success_count += 1

                console.print(
                    f"\n[bold green]=== Standalone Photo Album Scraping Completed ({success_count}/{len(album_targets)} successful) == =[ / bold green]")

        finally:
            await context.close()

    elapsed = time.time() - start_time
    if all_results:
        print_summary_table(all_results, elapsed)
    else:
        console.print(f"\n[bold green]Done! Total elapsed time: {elapsed:.1f}s[/bold green]")


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[bold red]Operation cancelled by user.[/bold red]")
        sys.exit(1)
    except Exception as e:
        if "AuthenticationError" in type(e).__name__:
            console.print(f"\n[bold red]❌ Error: Unable to download private photos. {e}[/bold red]")
            console.print(
                "[bold red]Please run 'lj-scraper --login' to authenticate first, or check your login session.[/bold red]")
            sys.exit(1)
        raise e