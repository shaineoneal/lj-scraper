import os
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from rich.panel import Panel
from .config import console

async def launch_browser_with_fallback(p, user_data_dir: str, headless: bool, args: list):
    """Tries to launch bundled Chromium, falling back to system Chrome/Chromium on failure."""
    # 1. Try bundled Chromium first
    try:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            args=args,
            ignore_https_errors=True
        )
        return context
    except Exception as e:
        import sys
        if getattr(sys, 'frozen', False):
            console.print("[yellow]Bundled Chromium failed to launch (possibly due to missing Linux OS dependencies).[/yellow]")
        else:
            console.print(f"[yellow]Default Chromium launch failed: {e}[/yellow]")
            
    # 2. Try system Google Chrome
    try:
        console.print("[blue]Attempting to launch system-installed Google Chrome...[/blue]")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            channel="chrome",
            args=args,
            ignore_https_errors=True
        )
        return context
    except Exception as e:
        pass

    # 3. Try system Chromium
    try:
        console.print("[blue]Attempting to launch system-installed Chromium...[/blue]")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            channel="chromium",
            args=args,
            ignore_https_errors=True
        )
        return context
    except Exception as e:
        pass

    # 4. If all fail, print troubleshooting options
    console.print("\n[bold red]❌ Browser launch failed completely.[/bold red]")
    if os.name != 'nt':
        console.print(
            "[bold yellow]If you are on Linux, you are likely missing required system libraries (e.g., libgbm, libatk, libasound).\n"
            "To fix this, choose the option for your Linux distribution:\n\n"
            "  1. If you are on Ubuntu/Debian (apt-get):\n"
            "     Run this executable with the --install-deps flag to install them automatically:\n"
            "     ./lj-scraper --install-deps\n\n"
            "  2. If you are on Fedora/RHEL/CentOS (dnf):\n"
            "     Install Chromium using dnf (this automatically handles all OS libraries):\n"
            "     sudo dnf install -y chromium\n\n"
            "  3. If you are on Arch Linux (pacman):\n"
            "     Install Chromium using pacman:\n"
            "     sudo pacman -S --noconfirm chromium\n[/bold yellow]"
        )
    raise Exception("Could not launch any browser. Please install Chrome/Chromium or run 'playwright install-deps'.")

async def run_login_flow(user_data_dir: str):
    """Launches browser to let the user log in (headed or headlessly via username/password)."""

    async with async_playwright() as p:
        console.print(Panel.fit(
            "A browser window has opened. Please log in to your LiveJournal account and then close the browser to save your session data for future scraping runs.\n\n"
            f"[dim]Session data will be saved to:[/dim] [bold green]{Path(user_data_dir).resolve()}[/bold green]\n"
            f"[dim]If you want to use a different directory for session data, set the USER_DATA_DIR environment variable or use the --user-data-dir flag when running the script.[/dim]",
            title="[bold blue]Login Flow[/bold blue]\n\n",
            border_style="blue"
        ))

        # Launch persistent context
        context = await launch_browser_with_fallback(
            p,
            user_data_dir=user_data_dir,
            headless=False,
            args=["--disable-dev-shm-usage"]
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.livejournal.com/login.bml")
            
        # Use an event listener to detect when the context is closed
        closed_event = asyncio.Event()
        context.on("close", lambda ctx: closed_event.set())

        # Also register a handler on browser disconnect if available
        if context.browser:
            context.browser.on("disconnected", lambda b: closed_event.set())

        # Wait until closed
        await closed_event.wait()
        console.print("[bold green]Browser closed. Session data saved successfully![/bold green]")