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
            args=args
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
            args=args
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
            args=args
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

async def run_login_flow(user_data_dir: str, username: str = None, password: str = None):
    """Launches browser to let the user log in (headed or headlessly via username/password)."""
    is_headless = username is not None and password is not None
    
    async with async_playwright() as p:
        if not is_headless:
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
                args=["--no-sandbox", "--disable-dev-shm-usage"]
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
        else:
            console.print(f"[bold blue]Performing headless login for user: {username}...[/bold blue]")
            
            # Launch persistent context headless
            context = await launch_browser_with_fallback(
                p,
                user_data_dir=user_data_dir,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = context.pages[0] if context.pages else await context.new_page()
            
            try:
                await page.goto("https://www.livejournal.com/login.bml")
                # Wait for user and password fields
                await page.wait_for_selector("input[name='user']", timeout=15000)
                await page.wait_for_selector("input[name='password']", timeout=15000)
                
                # Fill credentials
                await page.fill("input[name='user']", username)
                await page.fill("input[name='password']", password)
                
                # Click submit
                submit_selector = "button.b-loginform-btn--auth"
                await page.wait_for_selector(submit_selector, timeout=5000)
                
                # Click submit and wait for navigation
                await asyncio.gather(
                    page.click(submit_selector),
                    page.wait_for_load_state("networkidle", timeout=15000)
                )
                
                # Verify if we are logged in
                current_url = page.url
                if "login.bml" in current_url:
                    error_el = await page.query_selector(".b-loginform-error, .b-loginform-field__error")
                    if error_el:
                        err_text = await error_el.text_content()
                        raise Exception(f"Login failed: {err_text.strip()}")
                    else:
                        raise Exception("Login failed. Check your username and password.")
                
                console.print("[bold green]Successfully logged in programmatically! Session data saved.[/bold green]")
            except Exception as e:
                console.print(f"[bold red]Headless login failed: {e}[/bold red]")
                raise e
            finally:
                await context.close()
