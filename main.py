import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from virtual_lan.cli import main

if __name__ == "__main__":
    sys.exit(main())
