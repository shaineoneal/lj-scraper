import os
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from rich.panel import Panel
from .config import console

async def run_login_flow(user_data_dir: str):
    """Launches browser in headed mode to let the user log in and cache credentials."""
    async with async_playwright() as p:
        console.print(Panel.fit(
            "A browser window has opened. Please log in to your LiveJournal account and then close the browser to save your session data for future scraping runs.\n\n"
            f"[dim]Session data will be saved to:[/dim] [bold green]{Path(user_data_dir).resolve()}[/bold green]\n"
            f"[dim]If you want to use a different directory for session data, set the USER_DATA_DIR environment variable or use the --user-data-dir flag when running the script.[/dim]",
            title="[bold blue]Login Flow[/bold blue]\n\n",
            border_style="blue"
        ))

        # Launch persistent context
        context = await p.chromium.launch_persistent_context(user_data_dir, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.livejournal.com/")

        # Use an event listener to detect when the context is closed
        closed_event = asyncio.Event()
        context.on("close", lambda ctx: closed_event.set())

        # Also register a handler on browser disconnect if available
        if context.browser:
            context.browser.on("disconnected", lambda b: closed_event.set())

        # Wait until closed
        await closed_event.wait()
        console.print("[bold green]Browser closed. Session data saved successfully![/bold green]")
