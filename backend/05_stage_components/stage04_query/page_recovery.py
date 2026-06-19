from __future__ import annotations

from typing import Any

from .navigation import ensure_schedule_context, go_home_or_top


async def reset_for_next_probe(ctx: Any, reason: str) -> None:
    await go_home_or_top(ctx, reason)


async def retry_schedule_entry(ctx: Any, reason: str, *, max_attempts: int = 1) -> tuple[bool, Any, dict[str, Any]]:
    await go_home_or_top(ctx, reason)
    return await ensure_schedule_context(ctx, max_attempts=max_attempts)
