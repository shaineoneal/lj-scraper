import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright
from rich.panel import Panel

from .config import update_status


async def launch_browser_with_fallback(p, user_data_dir: str, headless: bool, args: list):
    """Tries to launch bundled Chromium, falling back to system Chrome/Chromium on failure."""
    update_status("Launching browser...")
    # 1. Try bundled Chromium first
    try:
        update_status("Launching bundled Chromium...")
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
            print("[$text-warning]Bundled Chromium failed to launch (possibly due to missing Linux OS dependencies).[/$text-warning]")
        else:
            print(f"[$text-warning]Default Chromium launch failed: {e}[/$text-warning]")

    # 2. Try system Google Chrome
    try:
        update_status("Launching system-installed Google Chrome...")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            channel="chrome",
            args=args,
            ignore_https_errors=True
        )
        return context
    except Exception:
        pass

        # 3. Try system Chromium
        try:
            update_status("Attempting to launch system-installed Chromium...")
            context = await p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=headless,
                channel="chromium",
                args=args,
                ignore_https_errors=True
            )
            return context
        except Exception:
            pass

    # 4. If all fail, print troubleshooting options
    print("\n[bold red]❌ Browser launch failed completely.[/bold red]")
    if os.name != 'nt':
        print(
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
    """Launches browser to let the user log in and automatically saves/closes upon successful login."""
    from .utils import get_logged_in

    async with async_playwright() as p:
        print(Panel.fit(
            "A browser window has opened. Please log in to your LiveJournal account.\n\n"
            "Once logged in, the window will automatically close and save your session data (or you can close it manually).\n\n"
            f"[dim]Session data will be saved to:[/dim] [bold $success]{Path(user_data_dir).resolve()}[/bold $success]\n"
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

        # Track browser window closure
        closed_event = asyncio.Event()
        context.on("close", lambda ctx: closed_event.set())
        if context.browser:
            context.browser.on("disconnected", lambda b: closed_event.set())

        logged_in_user = None

        # Poll for successful authentication until logged in or browser closed
        while not closed_event.is_set():
            try:
                cookies = await context.cookies("https://www.livejournal.com")
                has_session = any(c["name"] in ("ljloggedin", "ljmastersession") and c["value"] for c in cookies)

                for p_active in context.pages:
                    if not p_active.is_closed():
                        user = await get_logged_in(p_active)
                        if user:
                            logged_in_user = user
                            break

                if logged_in_user or has_session:
                    # Give cookies & storage a moment to finish syncing to disk
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                pass

            # Wait 1s between polling iterations or wake up if window is closed
            try:
                await asyncio.wait_for(asyncio.shield(closed_event.wait()), timeout=1.0)
            except asyncio.TimeoutError:
                pass

        # Close the context automatically if it's still open
        if not closed_event.is_set():
            try:
                await context.close()
            except Exception:
                pass

        if logged_in_user:
            print(f"[bold $success]✓ Logged in as {logged_in_user}! Session data saved successfully.[/bold $success]")
        else:
            print("[bold $success]Browser closed. Session data saved successfully![/bold $success]")