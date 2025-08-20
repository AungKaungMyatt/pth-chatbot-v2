from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
JSONL_PATH = LOG_DIR / "requests.jsonl"

_lock = Lock()

def _to_jsonable(v: Any) -> Any:
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except Exception:
        return str(v)

def log_event(event_type: str, **fields: Any) -> None:
    """
    Append one JSON line:
      ts, type, ...fields
    NOTE: make sure caller redacts secrets before logging.
    """
    rec: Dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type}
    rec.update({k: _to_jsonable(v) for (k, v) in fields.items()})
    line = json.dumps(rec, ensure_ascii=False)
    with _lock:
        JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with JSONL_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

def tail_jsonl(n: int = 200) -> List[Dict[str, Any]]:
    if not JSONL_PATH.exists():
        return []
    # Simple tail (OK for dev). For huge files, replace with a seek-based tail.
    with JSONL_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    out: List[Dict[str, Any]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out