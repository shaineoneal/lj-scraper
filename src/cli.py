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

from .config import console, DEFAULT_USER_DATA_DIR, DEFAULT_SETTINGS, load_config
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
    parser.add_argument("--entries", nargs="*", choices=["html", "pdf", "both", "none"], help="Scrape and download entries.")
    parser.add_argument("--profile", nargs="*", choices=["html", "pdf", "both", "none"], help="Scrape and download profiles.")
    parser.add_argument("--tags", nargs="*", choices=["html", "pdf", "both", "none"], help="Scrape and download tags.")
    parser.add_argument("--userpics", nargs="*", choices=["html", "pdf", "both", "none"], help="Scrape and download userpics.")
    parser.add_argument("--vgifts", nargs="*", choices=["html", "pdf", "both", "none"], help="Scrape and download vgifts.")
    parser.add_argument("--memories", nargs="*", choices=["html", "pdf", "both", "none"], help="Scrape and download memories.")
    parser.add_argument("--photos", nargs="*", choices=["html", "pdf", "both", "none"], help="Scrape and download photo albums and photos.")

    parser.add_argument("--max-memories", type=int, nargs="?", const=True, default=750,
                        help="Maximum number of memories to scrape (default: 750).")
    parser.add_argument("--max-dl-memories", type=int, nargs="?", const=True, default=500,
                        help="Maximum number of memories to download (default: 500).")

    args = parser.parse_args()

    settings = load_config(args.config)

    # Sync parsed args back into config.settings
    settings.update({k: v for k, v in vars(args).items() if v is not None})

    # Selective task overrides from command line
    tasks = ["entries", "profile", "tags", "userpics", "vgifts", "memories", "photos"]
    cli_flags = [f"--{task}" for task in tasks]
    if any(flag in sys.argv for flag in cli_flags):
        for task in tasks:
            if f"--{task}" in sys.argv:
                val = getattr(args, task)
                if isinstance(val, list) and ["none"] in val:
                    settings[task] = ["none"]
                elif isinstance(val, list) and val:
                    settings[task] = val
                else:
                    cfg_val = settings.get(task)
                    settings[task] = cfg_val if cfg_val in (["html"], ["pdf"], ["both"]) else ["both"]
            else:
                settings[task] = ["none"]

    # Resolve target
    target = args.target or args.target_opt
    if target:
        settings["target"] = target

    from .tui import LiveJournalScraperApp
    app = LiveJournalScraperApp(initial_settings=settings)
    await app.run_async()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[bold red]Operation cancelled by user.[/bold red]")
        sys.exit(0)
    except Exception as e:
        if "AuthenticationError" in type(e).__name__:
            console.print(f"\n[bold red]❌ Error: Unable to download private photos. {e}[/bold red]")
            console.print(
                "[bold red]Please run 'lj-scraper --login' to authenticate first, or check your login session.[/bold red]")
            sys.exit(1)
        raise e