import sys
from pathlib import Path

# Ensure backend/ is importable when running pytest from repo root
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
