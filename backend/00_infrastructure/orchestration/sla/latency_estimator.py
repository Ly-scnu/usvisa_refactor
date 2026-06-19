from __future__ import annotations

from importlib import import_module
from statistics import mean
from typing import Any

clock = import_module("00_infrastructure.orchestration.scheduler_clock")


class QueryLatencyEstimator:
    """Estimate business query latency from local event stream.

    It pairs smart_query_reserved -> smart_query_completed by lease_id.  This is
    read-only evidence used to launch the next query early enough so the result
    lands near the target success time.
    """

    def __init__(self, store: Any, config: Any):
        self.store = store
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    def estimate(self, limit: int = 1200) -> dict[str, Any]:
        default = float(getattr(self.cfg, "query_launch_lead_seconds", 12.0) or 12.0)
        lo = float(getattr(self.cfg, "query_launch_lead_min_seconds", 5.0) or 5.0)
        hi = float(getattr(self.cfg, "query_launch_lead_max_seconds", 25.0) or 25.0)
        try:
            events = self.store.events_tail(limit) if self.store else []
        except Exception:
            events = []
        starts: dict[str, Any] = {}
        samples: list[float] = []
        for ev in events:
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            lease = str(payload.get("lease_id") or "")
            if not lease:
                continue
            et = str(ev.get("event_type") or "")
            ts = clock.parse_ts(ev.get("created_at") or ev.get("ts"))
            if not ts:
                continue
            if et == "smart_query_reserved":
                starts[lease] = ts
            elif et == "smart_query_completed" and lease in starts:
                delta = max(0.1, (ts - starts[lease]).total_seconds())
                if delta <= 120:
                    samples.append(round(delta, 2))
        samples = samples[-50:]
        if not samples:
            lead = default
            p75 = default
            avg = default
        else:
            ss = sorted(samples)
            idx = min(len(ss) - 1, int(len(ss) * 0.75))
            p75 = ss[idx]
            avg = mean(ss)
            lead = p75
        lead = max(lo, min(hi, float(lead)))
        return {
            "samples": samples,
            "sample_count": len(samples),
            "avg_seconds": round(float(avg), 2),
            "p75_seconds": round(float(p75), 2),
            "lead_seconds": round(float(lead), 2),
            "min_seconds": lo,
            "max_seconds": hi,
            "source": "events" if samples else "config_default",
        }
