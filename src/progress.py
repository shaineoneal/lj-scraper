# src/progress.py
import json
import os
import tempfile
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Status values - common across all scrapers
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_SAVED = "saved"  # or "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"  # e.g., community has no photos
STATUS_DELETED = "deleted"
STATUS_SUSPENDED = "suspended"
STATUS_RESTRICTED = "restricted"  # e.g., auth required

TERMINAL_STATUSES = {STATUS_SAVED, STATUS_DELETED, STATUS_SUSPENDED, STATUS_SKIPPED, STATUS_RESTRICTED}
RETRYABLE_STATUSES = {STATUS_FAILED, STATUS_PENDING}


def normalize_items(items: list[str]) -> list[str]:
    """Strip and dedupe, preserving order"""
    out, seen = [], set()
    for i in items or []:
        if not isinstance(i, str):
            continue
        i = i.strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def items_hash(items: list[str]) -> str:
    """SHA256 hash of normalized items for identity matching"""
    normalized = normalize_items(items)
    joined = "\n".join(normalized).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _parse_ts(ts: str | None) -> datetime | None:
    """Parse ISO8601 timestamp string to datetime"""
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        # Handle naive datetimes - assume UTC for backwards compat
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _now_iso() -> str:
    """Current UTC time in ISO8601 format"""
    return datetime.now(timezone.utc).isoformat()


class ProgressTracker:
    def __init__(self,
                 output_dir: Path | str,
                 name: str,  # e.g., "save_posts", "photo_scraper", "extras_scraper"
                 input_type: str = "unknown",  # "xlsx", "txt", "url", "user_list", etc.
                 input_path: str | Path | None = None,
                 input_items: list[str] | None = None,  # full list of items to track
                 max_attempts: int = 3,
                 stale_minutes: int = 15):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.username = self.output_dir.name
        self.name = name
        self.input_type = input_type
        self.input_path = str(input_path) if input_path is not None else None
        self.input_items_raw = list(input_items) if input_items is not None else []
        self.normalized_items = normalize_items(self.input_items_raw)
        self._hash = (
            hashlib.sha256(str(Path(self.input_path).resolve()).encode("utf-8")).hexdigest()
            if self.input_path else items_hash(self.input_items_raw)
        )
        self.max_attempts = max_attempts
        self.stale_minutes = stale_minutes
        # Progress file path
        self.path = self.output_dir / f".{self.username}_progress.{self._hash[:12]}.json"
        self.data: dict[str, Any] = {}
        self._loaded = False

    # Entry management
    def _entry(self, item_key: str) -> dict:
        """Get or create an entry for the given item key"""
        items = self.data.setdefault("items", {})
        if item_key not in items:
            items[item_key] = {
                "status": STATUS_PENDING,
                "attempts": 0,
                "output_files": [],
                "last_error": None,
                "metadata": {},
                "updated_at": _now_iso(),
            }
        return items[item_key]

    # Loading/saving
    def load_or_create(self) -> None:
        """Load existing progress or create fresh from input_items"""
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Validate basic structure
                if isinstance(loaded, dict) and loaded.get("version") == 1:
                    # Check if hash matches
                    input_info = loaded.get("input") or {}
                    if input_info.get("hash") == self._hash:
                        self.data = loaded
                        self._loaded = True
                        # Ensure all items exist in data
                        items_dict = self.data.setdefault("items", {})
                        for item in self.normalized_items:
                            if item not in items_dict:
                                items_dict[item] = {
                                    "status": STATUS_PENDING,
                                    "attempts": 0,
                                    "output_files": [],
                                    "last_error": None,
                                    "metadata": {},
                                    "updated_at": _now_iso()
                                }
                        self.save()
                        print("Previously completed posts will be skipped. Resuming from last progress...\n")
                        return
                # Mismatched or invalid - backup and create fresh
                backup_path = self.path.with_suffix(f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
                try:
                    os.replace(self.path, backup_path)
                except Exception:
                    pass
            except Exception:
                # Corrupt file - backup and create fresh
                backup_path = self.path.with_suffix(f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
                try:
                    os.replace(self.path, backup_path)
                except Exception:
                    pass

        # Create fresh
        items_dict = {}
        now = _now_iso()
        items = {k: {"status": STATUS_PENDING, "attempts": 0, "output_files": [], "last_error": None, "metadata": {},
                     "updated_at": now} for k in self.normalized_items}
        self.data = {
            "version": 1,
            "name": self.name,
            "input": {"type": self.input_type, "path": self.input_path, "hash": self._hash},
            "max_attempts": self.max_attempts,
            "updated_at": now,
            "items": items,
        }
        self.save()

    def save(self) -> None:
        """Atomic save to disk"""
        if not self.data:
            return
        self.data["updated_at"] = _now_iso()
        _atomic_write_json(self.path, self.data)

    # State management
    def mark_in_progress(self, item_key: str) -> None:
        """Mark item as in_progress, increment attempts"""
        e = self._entry(item_key)
        e["status"] = STATUS_IN_PROGRESS
        e["attempts"] = e.get("attempts", 0) + 1
        e["last_error"] = None
        e["updated_at"] = _now_iso()
        self.save()

    def mark_done(self, item_key: str, status: str = STATUS_SAVED, output_files: list[str] | None = None,
                  **metadata) -> None:
        """Mark as successfully completed with optional metadata"""
        e = self._entry(item_key)
        e["status"] = status
        if output_files is not None:
            e["output_files"] = [str(f) for f in output_files]
        if metadata:
            e.setdefault("metadata", {}).update(metadata)
        e["updated_at"] = _now_iso()
        self.save()

    def mark_failed(self, item_key: str, error: str | Exception, **metadata) -> None:
        """Mark as failed, truncate error"""
        e = self._entry(item_key)
        e["status"] = STATUS_FAILED
        msg = str(error)
        e["last_error"] = msg[:500]
        if metadata:
            e.setdefault("metadata", {}).update(metadata)
        e["updated_at"] = _now_iso()
        self.save()

    def mark_terminal(self, item_key: str, status: str, **metadata) -> None:
        """Mark as terminal non-success (deleted/suspended/skipped/restricted)"""
        e = self._entry(item_key)
        e["status"] = status
        if metadata:
            e.setdefault("metadata", {}).update(metadata)
        e["updated_at"] = _now_iso()
        self.save()

    # Querying
    def get_todo(self, include_failed_with_retries_left: bool = True) -> list[str]:
        """Get items that still need processing - retry anything not successfully saved"""
        items = self.data.get("items", {})
        todo = []
        for k in self.normalized_items:
            e = items.get(k)
            if not e:
                todo.append(k)
                continue
            s = e.get("status")
            if s in (STATUS_SAVED, STATUS_DELETED, STATUS_SUSPENDED, STATUS_SKIPPED, STATUS_RESTRICTED):
                continue  # these count as successful
            if s == STATUS_IN_PROGRESS:
                continue  # reset_stale_in_progress will handle
            todo.append(k)
        return todo

    def is_done(self, item_key: str) -> bool:
        """Check if item is in terminal success state"""
        items_dict = self.data.get("items", {}) if self.data else {}
        entry = items_dict.get(item_key)
        if not entry:
            return False
        status = entry.get("status")
        return status in (STATUS_SAVED, STATUS_DELETED, STATUS_SUSPENDED, STATUS_SKIPPED, STATUS_RESTRICTED)

    def should_skip(self, item_key: str) -> bool:
        """Check if item is in any terminal state"""
        s = self.get_status(item_key)
        return s in TERMINAL_STATUSES if s else False

    def get_status(self, item_key: str) -> str | None:
        e = (self.data.get("items") or {}).get(item_key)
        return e.get("status") if e else None

    def get_stats(self) -> dict:
        """Return counts by status"""
        items = self.data.get("items", {})
        stats: dict[str, int] = {}
        for k in self.normalized_items:
            s = (items.get(k) or {}).get("status", "missing")
            stats[s] = stats.get(s, 0) + 1
        return stats

    # Resume handling
    def reset_stale_in_progress(self) -> int:
        """Reset in_progress items older than stale_minutes back to pending"""
        if not self.data:
            return 0
        items = self.data.get("items", {})
        now = datetime.now(timezone.utc)
        changed = 0
        for e in items.values():
            if e.get("status") != STATUS_IN_PROGRESS:
                continue
            ts = _parse_ts(e.get("updated_at"))
            if not ts or (now - ts) > timedelta(minutes=self.stale_minutes):
                e["status"] = STATUS_PENDING
                e["updated_at"] = _now_iso()
                changed += 1
        if changed:
            self.save()
        return changed

    def delete(self):
        """Delete the progress file"""
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass
