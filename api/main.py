from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "kwanza-ai-core"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.main import app  # noqa: E402
