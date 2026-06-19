from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from importlib import import_module
from typing import Any


clock = import_module("00_infrastructure.orchestration.scheduler_clock")
PeakWindowPolicy = import_module("00_infrastructure.orchestration.sla.peak_windows").PeakWindowPolicy


@dataclass(frozen=True)
class CooldownDecision:
    next_query_at: str
    cooldown_seconds: float
    mode: str
    reason: str
    anchor_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _next_anchor_after(base: datetime, *, minute: int) -> datetime:
    cur = base.replace(minute=minute, second=0, microsecond=0)
    if cur <= base:
        cur = cur + timedelta(hours=1)
    return cur


def next_release_anchor(now: datetime | None = None) -> datetime:
    """Return the next :00 or :30 release anchor after now."""
    now = now or clock.now_dt()
    a00 = _next_anchor_after(now, minute=0)
    a30 = _next_anchor_after(now, minute=30)
    return min(a00, a30)


class DynamicCooldownPolicy:
    """Calculate per-session next query time.

    The per-session gap is no longer a hard 180s wall.  It remains the default
    when the hot pool is healthy, but the scheduler can compress it when there
    is no candidate to bridge the next global query window.  Near :00/:30 the
    next query is aligned to the release anchor instead of blindly using
    ``last_success + 30s``.
    """

    def __init__(self, config: Any, store: Any):
        self.config = config
        self.store = store
        self.cfg = getattr(config, "smart_orchestrator", None)

    def _pool(self) -> dict[str, Any]:
        try:
            return (self.store.sla_state() or {}).get("pool") or {}
        except Exception:
            return {}

    def _peak(self) -> dict[str, Any]:
        try:
            return PeakWindowPolicy(self.config).current(clock.now_dt()).to_dict()
        except Exception:
            return {"mode": "normal"}

    def after_success(self, *, now: datetime | None = None, base_gap_seconds: float = 180.0, jitter_seconds: float = 0.0) -> CooldownDecision:
        now = now or clock.now_dt()
        peak = self._peak()
        peak_mode = str(peak.get("mode") or "normal")
        pool = self._pool()
        hot = int(pool.get("hot_sessions") or 0)
        query_wait = int(pool.get("query_wait_sessions") or 0)
        cooling = int(pool.get("cooling_sessions") or 0)
        candidates = hot + query_wait
        needed = int(pool.get("needed_hot_sessions") or getattr(self.cfg, "target_hot_query_sessions", 4) or 4)
        target_interval = float(pool.get("target_interval_seconds") or getattr(self.cfg, "normal_target_result_interval_seconds", 30.0) or 30.0)

        if peak_mode == "peak":
            gap = max(1.0, float(getattr(self.cfg, "release_per_session_min_query_interval_seconds", 1.0) or 1.0))
            return CooldownDecision(clock.add_seconds(now, gap), gap, "release_burst", "放票期：允许秒级复用，但全局闸门同秒只放一个业务 API")

        anchor = next_release_anchor(now)
        seconds_to_anchor = max(0.0, (anchor - now).total_seconds())
        # If a query happened shortly before :00/:30, prefer aligning its next
        # reuse to just after the anchor.  This implements the user's example:
        # queried at :58 -> do not wait arbitrary 30s; target :00+.
        if 0 < seconds_to_anchor <= 125:
            at = anchor + timedelta(seconds=0.5)
            return CooldownDecision(at.isoformat(timespec="seconds"), (at - now).total_seconds(), "release_anchor_align", "临近 00/30：把下一次复用对齐到关键分钟后", at.isoformat(timespec="seconds"))

        if candidates <= 0:
            gap = max(10.0, min(30.0, target_interval))
            return CooldownDecision(clock.add_seconds(now, gap), gap, "hot_pool_empty", "热查询池为空：成功会话快速补位，避免查询空窗")

        if candidates + cooling < max(2, min(needed, 6)):
            gap = max(20.0, min(45.0, target_interval))
            return CooldownDecision(clock.add_seconds(now, gap), gap, "hot_pool_low", "热池不足：成功会话按目标间隔动态复用，等待候补衔接")

        gap = max(1.0, float(base_gap_seconds or 180.0) + float(jitter_seconds or 0.0))
        return CooldownDecision(clock.add_seconds(now, gap), gap, "normal", "热池可轮转：使用常规单会话冷却")

    def after_failure(self, *, now: datetime | None = None, base_gap_seconds: float = 180.0, jitter_seconds: float = 0.0) -> CooldownDecision:
        return self.after_failure_kind(now=now, base_gap_seconds=base_gap_seconds, jitter_seconds=jitter_seconds, failure_kind="")

    def after_failure_kind(
        self,
        *,
        now: datetime | None = None,
        base_gap_seconds: float = 180.0,
        jitter_seconds: float = 0.0,
        failure_kind: str = "",
    ) -> CooldownDecision:
        now = now or clock.now_dt()
        peak_mode = str(self._peak().get("mode") or "normal")
        kind = str(failure_kind or "").lower()
        if any(x in kind for x in ("rate_limited", "rate_limit_429", "429", "too many requests")):
            gap = float(
                getattr(
                    self.cfg,
                    "release_rate_limited_cooldown_seconds" if peak_mode == "peak" else "rate_limited_cooldown_seconds",
                    120.0,
                )
                or 120.0
            )
            return CooldownDecision(clock.add_seconds(now, max(1.0, gap)), max(1.0, gap), "rate_limited_failure", "429/限流会话退避：不允许刚限流的会话在放票期反复抢业务闸门")
        if "failed_to_fetch" in kind or "failed to fetch" in kind:
            gap = float(
                getattr(
                    self.cfg,
                    "release_failed_fetch_cooldown_seconds" if peak_mode == "peak" else "failed_fetch_cooldown_seconds",
                    120.0,
                )
                or 120.0
            )
            return CooldownDecision(clock.add_seconds(now, max(1.0, gap)), max(1.0, gap), "failed_fetch_failure", "同源 fetch 失败：快速释放闸门后该会话退避，避免长时间占用查询窗口")
        if "page view blocked" in kind or "page_view_blocked" in kind:
            gap = float(
                getattr(
                    self.cfg,
                    "release_page_view_blocked_cooldown_seconds" if peak_mode == "peak" else "page_view_blocked_cooldown_seconds",
                    60.0,
                )
                or 60.0
            )
            return CooldownDecision(clock.add_seconds(now, max(1.0, gap)), max(1.0, gap), "page_view_blocked_failure", "page-view 被拦截：该会话先恢复/退避，避免连续抢唯一业务 API 闸门")
        if "auth_or_cf" in kind:
            gap = float(
                getattr(
                    self.cfg,
                    "release_auth_or_cf_cooldown_seconds" if peak_mode == "peak" else "auth_or_cf_cooldown_seconds",
                    60.0,
                )
                or 60.0
            )
            return CooldownDecision(clock.add_seconds(now, max(1.0, gap)), max(1.0, gap), "auth_or_cf_failure", "业务 API 被登录/CF 层拦截：该会话先恢复/退避，候补健康会话接力")
        if any(x in kind for x in ("page view blocked", "auth_or_cf", "page_view_blocked")):
            # Release windows may query every second globally, but a session
            # that just proved page-view/auth/rate-limit bad must not be the
            # same session repeatedly taking that one global slot.
            fallback_peak = float(getattr(self.cfg, "release_bad_session_cooldown_seconds", 30.0) or 30.0)
            fallback_normal = float(getattr(self.cfg, "bad_session_cooldown_seconds", 90.0) or 90.0)
            gap = max(1.0, fallback_peak if peak_mode == "peak" else fallback_normal)
            return CooldownDecision(clock.add_seconds(now, gap), gap, "bad_session_failure", "坏会话业务失败：退避该会话，避免连续抢唯一业务 API 闸门")
        if any(x in kind for x in ("callback_not_found", "page_not_found", "1015", "access_denied", "network_error")):
            gap = max(10.0, float(getattr(self.cfg, "terminal_session_cooldown_seconds", 180.0) or 180.0))
            return CooldownDecision(clock.add_seconds(now, gap), gap, "terminal_failure", "终止/高风险错误：当前会话应被回收，不参与短期业务查询")
        if peak_mode == "peak":
            gap = max(1.0, float(getattr(self.cfg, "release_failure_cooldown_seconds", 1.0) or 1.0))
            return CooldownDecision(clock.add_seconds(now, gap), gap, "release_failure_fast", "放票期失败快速让出候选位，候补接上")
        gap = max(5.0, float(base_gap_seconds or 60.0) + float(jitter_seconds or 0.0))
        return CooldownDecision(clock.add_seconds(now, gap), gap, "failure", "失败会话退避，避免反复抢唯一业务 API 闸门")
