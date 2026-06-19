import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright
from rich.panel import Panel

from .config import console, DEFAULT_USER_DATA_DIR
from .browser import run_login_flow, launch_browser_with_fallback
from .account_scraper import LiveJournalAccount
from .photo_scraper import LiveJournalPhotoScraper
from .utils import parse_targets, print_summary_table

async def main_async():
    parser = argparse.ArgumentParser(description="Scrape and download LiveJournal accounts and photo albums.")
    parser.add_argument("target", nargs="?", help="A LiveJournal profile URL, username, photo album URL, or .txt file containing them.")
    parser.add_argument("--user-data-dir", default=None, help=f"Directory for browser session data (default: read from USER_DATA_DIR env var or '{DEFAULT_USER_DATA_DIR}')")
    parser.add_argument("--login", action="store_true", help="Launch browser to log in and save session credentials.")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode (visible window).")
    parser.add_argument("--delay", type=float, default=0.0, help="Time in seconds to wait before page actions or downloads (default: 0.0)")
    parser.add_argument("--username", help="LiveJournal username for headless login.")
    parser.add_argument("--password", help="LiveJournal password for headless login.")
    parser.add_argument("--install-deps", action="store_true", help="Install missing Linux system dependencies for Playwright.")
    
    # Selective profile scraping flags
    parser.add_argument("--entries", action="store_true", help="Scrape recent entries")
    parser.add_argument("--profile", action="store_true", help="Scrape user profile")
    parser.add_argument("--tags", action="store_true", help="Scrape tags")
    parser.add_argument("--userpics", action="store_true", help="Scrape userpics")
    parser.add_argument("--vgifts", action="store_true", help="Scrape virtual gifts")
    parser.add_argument("--memories", action="store_true", help="Scrape memories")
    parser.add_argument("--photos", action="store_true", help="Scrape photos (downloads metadata and photo albums)")

    args = parser.parse_args()

    if args.install_deps:
        console.print("[bold blue]Installing missing Linux system dependencies for Playwright...[/bold blue]")
        import sys
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
    user_data_dir = args.user_data_dir or os.environ.get("USER_DATA_DIR", DEFAULT_USER_DATA_DIR)
    os.environ["USER_DATA_DIR"] = user_data_dir

    if args.login:
        username = args.username
        password = args.password
        
        # If no display is available (headless environment) and no credentials provided, prompt the user
        is_headless_env = os.name != 'nt' and not os.environ.get("DISPLAY")
        if is_headless_env and (not username or not password):
            console.print("[yellow]Headless environment detected. Please enter your LiveJournal credentials to log in programmatically.[/yellow]")
            import getpass
            if not username:
                username = input("Username: ").strip()
            if not password:
                password = getpass.getpass("Password: ")
                
        await run_login_flow(user_data_dir, username, password)
        return

    if not args.target:
        parser.print_help()
        return

    # Parse targets into profile usernames and specific album URLs
    profile_targets, album_targets = parse_targets(args.target)

    if not profile_targets and not album_targets:
        console.print("[bold red]Please provide a valid LiveJournal profile URL, username, album URL, or text file of targets as an argument.[/bold red]")
        sys.exit(1)

    # Profile scraping options
    options = {
        "entries": args.entries,
        "profile": args.profile,
        "tags": args.tags,
        "userpics": args.userpics,
        "vgifts": args.vgifts,
        "memories": args.memories,
        "photos": args.photos
    }

    # If no specific profile options are selected, enable all by default
    if not any(options.values()):
        options = {k: True for k in options}

    start_time = time.time()
    all_results = []
    headless = not args.headed

    async with async_playwright() as p:
        console.print(f"[bold blue]Launching browser context from session directory: {Path(user_data_dir).resolve()}[/bold blue]")
        context = await launch_browser_with_fallback(
            p,
            user_data_dir=user_data_dir,
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        
        try:
            # 1. Process profile scraping targets
            if profile_targets:
                console.print(f"\n[bold green]=== Starting profile scraping for {len(profile_targets)} account(s) ===[/bold green]")
                for username in profile_targets:
                    console.print(f"\n[bold magenta]► Processing LJ account:[/bold magenta] {username}")
                    lj_user = LiveJournalAccount(context, username, options, delay=args.delay)
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
                console.print(f"\n[bold green]=== Starting photo album downloads for {len(album_targets)} album(s) ===[/bold green]")
                photo_scraper = LiveJournalPhotoScraper(context, headless=headless, delay=args.delay)
                success_count = 0
                for idx, album_url in enumerate(album_targets):
                    console.print(f"\n[bold magenta]► Processing Album {idx+1}/{len(album_targets)}:[/bold magenta] {album_url}")
                    ok = await photo_scraper.scrape_album(album_url)
                    if ok:
                        success_count += 1
                
                console.print(f"\n[bold green]=== Standalone Photo Album Scraping Completed ({success_count}/{len(album_targets)} successful) ===[/bold green]")

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
