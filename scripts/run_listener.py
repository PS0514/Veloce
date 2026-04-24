import os
os.environ["VELOCE_SERVICE_NAME"] = "telegram"

from pathlib import Path
import sys
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

from veloce.orchestrator.logging_utils import configure_logging, get_logger, log_info

configure_logging()
logger = get_logger(__name__)

if __name__ == "__main__":
    log_info(logger, "telegram_bootstrap_start", host="0.0.0.0", port=8003)
    uvicorn.run("veloce.services.telegram.main:app", host="0.0.0.0", port=8003, reload=False, log_config=None)
