from pathlib import Path
import sys

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


if __name__ == "__main__":
    uvicorn.run("veloce.orchestrator.app:app", host="0.0.0.0", port=8000, reload=False)
