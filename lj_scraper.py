import os
import sys

# If running inside a PyInstaller executable, point Playwright to the bundled browser binaries
if getattr(sys, 'frozen', False):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(sys._MEIPASS, "ms-playwright")

from src.cli import main

if __name__ == "__main__":
    main()