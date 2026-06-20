from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import fnmatch
import os
from pathlib import Path
import shutil
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..dependencies import container

router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])


@dataclass(frozen=True)
class CleanupCategory:
    key: str
    label: str
    description: str
    roots: tuple[str, ...]
    patterns: tuple[str, ...]
    default_enabled: bool = True
    dangerous: bool = False


CATEGORIES = (
    CleanupCategory("logs", "普通日志", "storage/logs 下的 .log/.txt 运行日志；默认不碰 events.jsonl。", ("storage/logs",), ("*.log", "*.txt", "*.out", "*.err")),
    CleanupCategory("screenshots", "截图与快照", "live_snapshots 与 debug 目录中的 png/jpg/webp 截图。", ("storage/live_snapshots", "storage/debug_login", "storage/debug_query", "storage/debug_cf", "storage/debug"), ("*.png", "*.jpg", "*.jpeg", "*.webp")),
    CleanupCategory("debug_artifacts", "调试产物", "debug 目录中的 html/json/txt/meta 证据文件。", ("storage/debug_login", "storage/debug_query", "storage/debug_cf", "storage/debug"), ("*.html", "*.json", "*.txt", "*.mhtml")),
    CleanupCategory("temp", "临时文件", "项目根目录与 storage/tmp/cache 中的临时 JSON、tmp、cache 文件。", (".", "storage/tmp", "storage/cache"), ("temp*.json", "*_sample.json", "*_status*.json", "*.tmp", "*.cache")),
    CleanupCategory("browser_profiles", "浏览器画像", "旧浏览器 profile / context / user-data-dir。可能包含 Cookie，运行时不建议清理。", ("storage/browser_profiles", "storage/profiles", "browser_profiles"), ("*",), False, True),
    CleanupCategory("runtime_history", "运行历史 JSONL", "events.jsonl、ticket_history.jsonl、query_success_records.jsonl；会影响历史分析。", ("storage/logs", "storage/runtime"), ("events.jsonl", "ticket_history.jsonl", "query_success_records.jsonl"), False, True),
    CleanupCategory("frontend_build", "前端构建缓存", "frontend/dist 与 Vite 缓存。删除后需要重新构建。", ("frontend/dist", "frontend/.vite"), ("*",), False, False),
)

PROTECTED_NAMES = {
    ".git", "node_modules", "app.db", "app.db-wal", "app.db-shm",
    "pipeline_status.json", "slot_status.json", "account_guard.json",
    "login_admission.json", "sla_orchestrator_state.json", "route_health.json",
    "latest_ticket.json", "booking_signal.json", "availability.txt",
}
MAX_MATCHES_PER_ROOT = 300
MAX_SIZE_WALK_FILES = 1200
MAX_SCAN_VISITS_PER_ROOT = 1500


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _project_root() -> Path:
    return Path(container()["config"].project_root).resolve()


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _age_days(path: Path, now: float) -> float:
    try:
        return max(0.0, (now - path.stat().st_mtime) / 86400)
    except Exception:
        return 0.0


def _fmt_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(size)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{size} B"


def _bounded_size(path: Path, max_files: int = MAX_SIZE_WALK_FILES) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        seen = 0
        stack = [path]
        while stack and seen < max_files:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for ent in it:
                        if seen >= max_files:
                            break
                        if ent.is_file(follow_symlinks=False):
                            total += ent.stat(follow_symlinks=False).st_size
                            seen += 1
                        elif ent.is_dir(follow_symlinks=False) and ent.name not in PROTECTED_NAMES:
                            stack.append(Path(ent.path))
            except Exception:
                pass
        return total
    except Exception:
        return 0


def _iter_matches(root: Path, category: CleanupCategory) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for pattern in category.patterns:
        try:
            if root.is_file() and fnmatch.fnmatch(root.name, pattern):
                out.append(root)
            elif pattern == "*":
                out.extend(list(root.iterdir())[:MAX_MATCHES_PER_ROOT])
            elif category.key == "temp" and root.name == ".":
                out.extend(list(root.glob(pattern))[:MAX_MATCHES_PER_ROOT])
            else:
                stack = [root]
                visits = 0
                while stack and visits < MAX_SCAN_VISITS_PER_ROOT and len(out) < MAX_MATCHES_PER_ROOT:
                    cur = stack.pop()
                    try:
                        with os.scandir(cur) as it:
                            for ent in it:
                                visits += 1
                                if visits >= MAX_SCAN_VISITS_PER_ROOT or len(out) >= MAX_MATCHES_PER_ROOT:
                                    break
                                p = Path(ent.path)
                                if fnmatch.fnmatch(ent.name, pattern):
                                    out.append(p)
                                if ent.is_dir(follow_symlinks=False) and ent.name not in PROTECTED_NAMES:
                                    stack.append(p)
                    except Exception:
                        pass
        except Exception:
            pass
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in out:
        try:
            rp = p.resolve()
        except Exception:
            continue
        if rp not in seen:
            seen.add(rp)
            unique.append(rp)
    return unique


def _category_map() -> dict[str, CleanupCategory]:
    return {c.key: c for c in CATEGORIES}


def _candidate_rows(categories: list[str] | None, retention_days: float) -> list[dict[str, Any]]:
    root = _project_root()
    now = time.time()
    selected = categories or [c.key for c in CATEGORIES if c.default_enabled]
    rows: list[dict[str, Any]] = []
    cmap = _category_map()
    for key in selected:
        category = cmap.get(key)
        if not category:
            continue
        for rel_root in category.roots:
            scan_root = (root / rel_root).resolve()
            if not _is_inside(scan_root, root):
                continue
            for path in _iter_matches(scan_root, category):
                if not _is_inside(path, root):
                    continue
                if path.name in PROTECTED_NAMES or any(part in PROTECTED_NAMES for part in path.parts):
                    continue
                if path in {root, root / "storage", root / "frontend"}:
                    continue
                age = _age_days(path, now)
                if age < retention_days:
                    continue
                size = _bounded_size(path)
                try:
                    rel = str(path.relative_to(root)).replace("\\", "/")
                except Exception:
                    rel = str(path)
                rows.append({
                    "category": category.key,
                    "category_label": category.label,
                    "path": rel,
                    "type": "dir" if path.is_dir() else "file",
                    "size": size,
                    "size_text": _fmt_size(size),
                    "age_days": round(age, 2),
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds") if path.exists() else "",
                    "dangerous": category.dangerous,
                })
    rows.sort(key=lambda x: (str(x.get("category")), -int(x.get("size") or 0), str(x.get("path"))))
    return rows


@router.get("/summary")
def cleanup_summary():
    root = _project_root()
    now = time.time()
    categories: list[dict[str, Any]] = []
    for category in CATEGORIES:
        files: list[Path] = []
        for rel_root in category.roots:
            scan_root = (root / rel_root).resolve()
            if _is_inside(scan_root, root):
                files.extend(_iter_matches(scan_root, category)[:MAX_MATCHES_PER_ROOT])
        safe = [p for p in files if _is_inside(p, root) and p.name not in PROTECTED_NAMES and not any(part in PROTECTED_NAMES for part in p.parts)]
        size = sum(_bounded_size(p, 300) for p in safe[:MAX_MATCHES_PER_ROOT])
        oldest = max([_age_days(p, now) for p in safe] or [0])
        categories.append({
            "key": category.key,
            "label": category.label,
            "description": category.description,
            "default_enabled": category.default_enabled,
            "dangerous": category.dangerous,
            "count": len(safe),
            "size": size,
            "size_text": _fmt_size(size),
            "oldest_age_days": round(oldest, 1),
        })
    storage_size = _bounded_size(root / "storage", 3000)
    return {
        "generated_at": _now_iso(),
        "project_root": str(root),
        "storage_size": storage_size,
        "storage_size_text": _fmt_size(storage_size),
        "categories": categories,
        "guardrails": {
            "inside_project_only": True,
            "protected_names": sorted(PROTECTED_NAMES),
            "default_retention_days": 3,
            "dangerous_categories_default_off": True,
        },
    }


@router.post("/preview")
def cleanup_preview(payload: dict[str, Any] = Body(default_factory=dict)):
    retention_days = max(0.0, float(payload.get("retention_days", 3)))
    categories = payload.get("categories")
    if categories is not None and not isinstance(categories, list):
        raise HTTPException(status_code=400, detail="categories must be a list")
    rows = _candidate_rows(categories, retention_days)
    total = sum(int(r.get("size") or 0) for r in rows)
    return {"generated_at": _now_iso(), "dry_run": True, "retention_days": retention_days, "count": len(rows), "total_size": total, "total_size_text": _fmt_size(total), "items": rows[:2000], "truncated": len(rows) > 2000}


@router.post("/run")
def cleanup_run(payload: dict[str, Any] = Body(default_factory=dict)):
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm=true is required")
    retention_days = max(0.0, float(payload.get("retention_days", 3)))
    categories = payload.get("categories")
    if categories is not None and not isinstance(categories, list):
        raise HTTPException(status_code=400, detail="categories must be a list")
    root = _project_root()
    deleted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for row in _candidate_rows(categories, retention_days):
        path = (root / str(row["path"])).resolve()
        if not _is_inside(path, root) or path.name in PROTECTED_NAMES or any(part in PROTECTED_NAMES for part in path.parts):
            failed.append({**row, "error": "protected or outside project"})
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            deleted.append(row)
        except Exception as exc:
            failed.append({**row, "error": str(exc)})
    total = sum(int(r.get("size") or 0) for r in deleted)
    return {"generated_at": _now_iso(), "dry_run": False, "retention_days": retention_days, "deleted_count": len(deleted), "failed_count": len(failed), "freed_size": total, "freed_size_text": _fmt_size(total), "deleted": deleted[:1000], "failed": failed[:200]}
