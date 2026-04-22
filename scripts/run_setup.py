from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from veloce.setup_wizard import run_setup_wizard

if __name__ == "__main__":
    run_setup_wizard()
