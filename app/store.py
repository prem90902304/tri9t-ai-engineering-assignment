"""Small, justified JSON document store for generated LLM output.
SQLite remains the source of truth for document/version/selection metadata.
"""
import json, os, tempfile
from pathlib import Path
from threading import RLock
from app.config import settings

_lock = RLock()
def _path():
    p = Path(settings.generated_store_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists(): p.write_text("[]", encoding="utf-8")
    return p

def all_generations():
    with _lock:
        return json.loads(_path().read_text(encoding="utf-8"))

def save_generation(item: dict):
    with _lock:
        data = json.loads(_path().read_text(encoding="utf-8"))
        data.append(item)
        p = _path()
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix="generated-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return item
