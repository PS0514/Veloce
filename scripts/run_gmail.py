import sys
import os
from pathlib import Path

# Set service name for logging before importing get_logger
os.environ["VELOCE_SERVICE_NAME"] = "gmail"

# Add src to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))

import uvicorn

if __name__ == "__main__":
    # Gmail service usually runs on port 8005
    uvicorn.run("veloce.services.gmail.main:app", host="0.0.0.0", port=8005, reload=False)
