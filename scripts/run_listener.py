from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

from veloce.listener_service import run_listener
from veloce.orchestrator.logging_utils import configure_logging, get_logger, log_info

configure_logging()
logger = get_logger(__name__)

if __name__ == "__main__":
    log_info(logger, "listener_bootstrap_start")
    run_listener()
