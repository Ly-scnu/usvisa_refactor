from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from fastapi import APIRouter, Query
from importlib import import_module
from statistics import mean
from typing import Any

from ..dependencies import container

router = APIRouter(prefix="/api/tickets", tags=["tickets"])
SmartQueryGate = import_module("00_infrastructure.orchestration.query_gate").SmartQueryGate
SlaOrchestrator = import_module("00_infrastructure.orchestration.sla_orchestrator").SlaOrchestrator


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _seconds(a: Any, b: Any) -> float:
    da, db = _parse_ts(a), _parse_ts(b)
    if not da or not db:
        return 0.0
    return max(0.0, round((db - da).total_seconds(), 2))


def _round_num(round_id: str) -> int:
    import re
    m = re.search(r"(\d+)", str(round_id or ""))
    return int(m.group(1)) if m else 0


def _short_time_from_ts(ts: Any) -> str:
    try:
        parsed = _parse_ts(ts)
        if parsed:
            return parsed.strftime("%H%M%S")
    except Exception:
        pass
    return str(ts or "unknown").replace(":", "").replace("-", "").replace("+", "").replace("T", "")[-6:] or "unknown"


def _stable_ticket_id(slot: Any, round_id: Any, round_started_at: Any = "") -> str:
    slot_s = str(slot or "").replace("slot_", "S") or "S--"
    round_s = str(round_id or "").replace("round_", "") or "----"
    return f"T-{round_s}-{slot_s}-{_short_time_from_ts(round_started_at)}"


def _stable_session_id(slot: Any, round_id: Any, round_started_at: Any = "") -> str:
    return f"{slot or '-'}-{round_id or '-'}-{_short_time_from_ts(round_started_at)}"


def _query_success_id(seq: Any = None, fallback_index: int | None = None) -> str:
    try:
        n = int(seq or 0)
        if n > 0:
            return f"Q-{n:06d}"
    except Exception:
        pass
    if fallback_index is not None:
        return f"Q-H{fallback_index:06d}"
    return "Q-unknown"


def _status_zh(code: str) -> str:
    return {
        "available": "待查询",
        "using": "使用中",
        "success": "查询成功",
        "used": "查询成功",
        "failed": "已失败",
        "paused": "暂停中",
        "manual": "待人工",
        "hit": "命中",
    }.get(code, code)


def _nearest_date(days: Any) -> str:
    values = [str(x)[:10] for x in (days or []) if str(x or "").strip()]
    values = [x for x in values if len(x) >= 10]
    return sorted(values)[0] if values else ""


def _last_result_zh(row: dict[str, Any]) -> str:
    if row.get("hit_count"):
        date = row.get("last_nearest_date") or row.get("last_selected_date") or ""
        return f"命中目标：{date}" if date else "命中目标"
    if row.get("query_success_count"):
        date = row.get("last_nearest_date") or ""
        count = int(row.get("last_query_days_count") or row.get("days_count") or 0)
        if date:
            return f"最近 {date}（{count}天）"
        return "无可用日期"
    text = " ".join(str(x or "") for x in [row.get("last_message"), row.get("last_reason"), row.get("official_error_code")]).lower()
    if "navigation_not_found" in text:
        return "未进入查询页"
    if "cf" in text or "challenge" in text:
        return "CF 校验失败"
    if "waiting" in text or "等待" in text:
        return "进入等待室"
    if "1015" in text:
        return "1015 限流"
    if "429" in text:
        return "429 限流"
    if "login" in text or "登录" in text:
        return "登录失败/回跳"
    return row.get("last_message") or "—"


def _stage_zh(stage: str) -> str:
    return {
        "proxy_acquire": "获取代理",
        "cf_gate": "CF 校验",
        "waiting_room": "等候室",
        "login": "登录/密保",
        "business_query": "查询日期/时间段",
        "booking_submit": "提交抢票",
        "round_start": "本轮开始",
        "round_finish": "本轮结束",
        "browser_launched": "拉起浏览器",
    }.get(stage or "", stage or "-")


def _payload_screenshot(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("screenshot"), str):
        return payload["screenshot"]
    pools = [
        payload.get("artifacts"),
        payload.get("payload", {}).get("artifacts") if isinstance(payload.get("payload"), dict) else None,
        payload.get("evidence"),
        payload.get("recovery", {}).get("evidence") if isinstance(payload.get("recovery"), dict) else None,
    ]
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        for key in ("final_screenshot", "screenshot", "snapshot"):
            value = pool.get(key)
            if isinstance(value, str):
                return value
    return ""


def _semantic_event(ev: dict[str, Any]) -> dict[str, Any] | None:
    payload = ev.get("payload") or {}
    et = str(ev.get("event_type") or "")
    stage = str(payload.get("stage") or ev.get("stage") or "")
    msg = str(payload.get("message") or payload.get("reason") or payload.get("stage_zh") or "")
    ts = ev.get("created_at") or ev.get("ts") or ""
    screenshot = _payload_screenshot(payload)

    # live_snapshot auto_10s/stage_enter/stage_exit are evidence, not business
    # lifecycle transitions. They are still available through raw events.jsonl.
    if et in {"live_snapshot", "stage_final_snapshot", "round_close_snapshot"}:
        return None
    if et == "stage_enter":
        if stage in {"cf_gate", "waiting_room", "login", "business_query", "booking_submit"}:
            return {"ts": ts, "event_type": et, "stage": stage, "title": f"开始{_stage_zh(stage)}", "message": msg or "stage_enter", "tone": "info", "screenshot": screenshot, "payload": payload}
        return None
    if et == "stage_exit":
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        ok = bool(payload.get("ok"))
        state = inner.get("state") if isinstance(inner.get("state"), dict) else {}
        title = f"{_stage_zh(stage)}{'完成' if ok else '失败'}"
        message = msg or str(inner.get("reason") or inner.get("needs_recover") or "")
        tone = "good" if ok else "bad"
        if stage == "cf_gate" and ok:
            message = f"通过，进入 {state.get('stage') or '-'}"
        if stage == "waiting_room" and ok:
            message = "未进入等候室" if "not in waiting" in message.lower() else message
        return {"ts": ts, "event_type": et, "stage": stage, "title": title, "message": message, "tone": tone, "screenshot": screenshot, "payload": payload}
    if et == "round_start":
        return {"ts": ts, "event_type": et, "stage": "round_start", "title": "本轮开始", "message": payload.get("mode") or "", "tone": "info", "screenshot": screenshot, "payload": payload}
    if et == "round_finish":
        ok = bool(payload.get("ok"))
        return {"ts": ts, "event_type": et, "stage": "round_finish", "title": "本轮结束", "message": payload.get("message") or "", "tone": "good" if ok else "bad", "screenshot": screenshot, "payload": payload}
    if et == "browser_launched":
        return {"ts": ts, "event_type": et, "stage": "browser_launched", "title": "拉起浏览器", "message": payload.get("profile") or "", "tone": "info", "screenshot": screenshot, "payload": payload}
    if et == "recovery_attempt":
        code = payload.get("error_type") or ""
        return {"ts": ts, "event_type": et, "stage": "recovery", "title": f"错误/恢复：{code}", "message": payload.get("message") or payload.get("action") or "", "tone": "bad", "screenshot": screenshot, "payload": payload}
    if et == "stage_recovery_retry":
        title = f"恢复后重试{_stage_zh(stage)}"
        message = f"第{payload.get('retry_no') or '-'}次；原因：{payload.get('needs_recover') or ''}"
        return {"ts": ts, "event_type": et, "stage": stage or "recovery", "title": title, "message": message, "tone": "info", "screenshot": screenshot, "payload": payload}
    if et.startswith("business_"):
        # Keep only business milestones/errors in the lifecycle. Low-level
        # "start/collecting/not_found click attempt" logs stay in raw_flow.
        if et in {"business_navigation_start", "business_post_selecting", "business_dates_collecting", "business_slot_collecting", "business_retry_home"}:
            return None
        if et == "business_manage_clicked" and payload.get("clicked") is False:
            return None
        title_map = {
            "business_navigation_start": "进入预约入口",
            "business_manage_clicked": "点击预约入口",
            "business_schedule_page_ready": "预约页就绪",
            "business_context_resolved": "申请上下文就绪",
            "business_post_selected": "选择北京完成",
            "business_dates_collected": "查询日期成功",
            "business_date_accepted": "日期命中目标",
            "business_date_rejected": "日期不合适",
            "business_entries_collected": "查询时间段完成",
            "business_booking_signal_ready": "抢票信号生成",
            "business_navigation_blocked": "进入查询失败",
            "business_blocked_cf": "CF 阻断",
            "business_blocked_login": "登录回跳",
            "business_rate_limit_cooldown": "限流冷却",
        }
        tone = "bad" if any(x in et for x in ("blocked", "rate_limit")) else ("good" if any(x in et for x in ("collected", "ready", "selected", "accepted")) else "info")
        return {"ts": ts, "event_type": et, "stage": "business_query", "title": title_map.get(et, et), "message": msg or str(payload.get("method") or payload.get("needs_recover") or ""), "tone": tone, "screenshot": screenshot, "payload": payload}
    return None


def _compact_flow(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    shots: list[tuple[str, str, str]] = []
    for ev in raw:
        payload = ev.get("payload") or {}
        et = str(ev.get("event_type") or "")
        stage = str(payload.get("stage") or ev.get("stage") or "")
        ts = str(ev.get("created_at") or ev.get("ts") or "")
        shot = _payload_screenshot(payload)
        if not shot:
            continue
        # Never bind storage/live_snapshots/*.png to historical lifecycle nodes:
        # that file is overwritten every 10s and made old CF/login/home events
        # show the wrong current page. Only immutable evidence screenshots are
        # used in the analysis lifecycle.
        if et == "stage_final_snapshot":
            shots.append((stage, ts, shot))
        elif et == "round_close_snapshot":
            shots.append(("round_finish", ts, shot))

    def nearest_shot(stage: str, ts: Any, max_delta: float = 5.0) -> str:
        t0 = _parse_ts(ts)
        if not t0:
            return ""
        best: tuple[float, str] | None = None
        for shot_stage, shot_ts, shot in shots:
            if shot_stage != stage:
                continue
            t1 = _parse_ts(shot_ts)
            if not t1:
                continue
            delta = abs((t1 - t0).total_seconds())
            if delta <= max_delta and (best is None or delta < best[0]):
                best = (delta, shot)
        return best[1] if best else ""

    for ev in raw:
        item = _semantic_event(ev)
        if not item:
            continue
        if not item.get("screenshot"):
            stage = str(item.get("stage") or "")
            if item.get("event_type") == "stage_exit":
                item["screenshot"] = nearest_shot(stage, item.get("ts"))
            elif item.get("event_type") == "round_finish":
                item["screenshot"] = nearest_shot("round_finish", item.get("ts"))
        key = (str(item.get("event_type")), str(item.get("stage")), str(item.get("message"))[:80])
        # Collapse exact duplicate semantic nodes, but allow repeated CF/login
        # cycles when the message or timestamp changes.
        if key in seen and item.get("event_type") == "stage_enter":
            continue
        seen.add(key)
        out.append(item)
    return out


def build_ticket_analytics(store: Any, *, limit: int = 3000) -> dict[str, Any]:
    events = store.events_tail(limit)
    history = store.ticket_history(limit)
    rows: dict[str, dict[str, Any]] = {}
    stage_enter: dict[tuple[str, str], str] = {}
    active_rounds: dict[tuple[str, str], str] = {}

    def _short_time(ts: str) -> str:
        try:
            return (_parse_ts(ts) or datetime.fromisoformat(str(ts))).strftime("%H%M%S")
        except Exception:
            return str(ts or "unknown").replace(":", "").replace("-", "")[-6:] or "unknown"

    def ensure(slot: str, round_id: str, instance_ts: str = "") -> dict[str, Any]:
        instance = instance_ts or "unknown"
        key = f"{slot}/{round_id}/{instance}"
        if key not in rows:
            suffix = _short_time(instance)
            rows[key] = {
                "key": key,
                "ticket_id": f"T-{str(round_id).replace('round_', '')}-{slot.replace('slot_', 'S')}-{suffix}",
                "session_id": f"{slot}-{round_id}-{suffix}",
                "session_label": f"{slot.replace('slot_', 'Slot ')} / 第{_round_num(round_id)}轮 / {_short_time(instance)}",
                "slot_id": slot,
                "round_id": round_id,
                "round_no": _round_num(round_id),
                "round_started_at": instance_ts or "",
                "account_alias": "default",
                "created_at": instance_ts or "",
                "updated_at": instance_ts or "",
                "status": "failed",
                "status_zh": "已失败",
                "proxy_display": "-",
                "proxy_session": "",
                "route": "-",
                "query_success_count": 0,
                "uses_count": 0,
                "days_count": 0,
                "last_query_days_count": 0,
                "last_nearest_date": "",
                "last_query_dates_preview": [],
                "last_selected_date": "",
                "slots_count": 0,
                "matched_count": 0,
                "hit_count": 0,
                "last_query_at": "",
                "last_result": "—",
                "last_message": "",
                "last_reason": "",
                "official_error_code": "",
                "alive_seconds": 0,
                "flow": [],
                "raw_flow": [],
                "stage_durations": {},
                "query_results": [],
                "recovery_count": 0,
            }
        return rows[key]

    def row_for_event(slot: str, round_id: str, ts: str, et: str) -> dict[str, Any]:
        sr = (slot, round_id)
        if et == "round_start" or sr not in active_rounds:
            row = ensure(slot, round_id, ts)
            active_rounds[sr] = row["key"]
            return row
        return rows[active_rounds[sr]]

    for ev in events:
        slot, round_id = ev.get("slot_id"), ev.get("round_id")
        if not slot or not round_id:
            continue
        ts = ev.get("created_at") or ""
        payload = ev.get("payload") or {}
        et = ev.get("event_type") or ""
        stage = payload.get("stage") or ev.get("stage") or ""
        msg = payload.get("message") or payload.get("reason") or payload.get("stage_zh") or ""
        row = row_for_event(str(slot), str(round_id), str(ts), str(et))
        if not row["created_at"] or ts < row["created_at"]:
            row["created_at"] = ts
        if not row.get("round_started_at") and et == "round_start":
            row["round_started_at"] = ts
        if not row["updated_at"] or ts > row["updated_at"]:
            row["updated_at"] = ts
        row["raw_flow"].append({"ts": ts, "created_at": ts, "event_type": et, "stage": stage, "message": msg, "payload": payload})
        if et == "stage_enter" and stage:
            stage_enter[(row["key"], str(stage))] = ts
        if et == "stage_exit" and stage:
            start = stage_enter.get((row["key"], str(stage)))
            dur = _seconds(start, ts)
            if dur:
                row["stage_durations"][str(stage)] = round(row["stage_durations"].get(str(stage), 0) + dur, 2)
            row["last_message"] = str(payload.get("message") or msg or row["last_message"])
            inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            row["last_reason"] = str(inner.get("reason") or inner.get("needs_recover") or row["last_reason"])
            rec = inner.get("recovery") if isinstance(inner.get("recovery"), dict) else {}
            official = (rec.get("evidence") or {}).get("official_error") if isinstance(rec.get("evidence"), dict) else {}
            if isinstance(official, dict) and official.get("code"):
                row["official_error_code"] = str(official.get("code"))
        if et == "stage_exit" and stage == "proxy_acquire":
            inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            row["proxy_display"] = inner.get("proxy_display") or row["proxy_display"]
            proxy = inner.get("proxy") if isinstance(inner.get("proxy"), dict) else {}
            if proxy:
                row["proxy_session"] = str(proxy.get("session_id") or proxy.get("session") or "")
                row["route"] = "/".join([str(proxy.get("country") or proxy.get("country_code") or ""), str(proxy.get("asn") or "")]).strip("/") or row["route"]
        if et == "recovery_attempt":
            row["recovery_count"] = int(row.get("recovery_count") or 0) + 1
            row["last_message"] = payload.get("message") or row["last_message"]
        if et == "round_finish":
            row["status"] = "success" if row.get("query_success_count") else "failed"
            row["last_message"] = payload.get("message") or row["last_message"]

    def row_for_ticket(ticket: dict[str, Any], idx: int) -> dict[str, Any]:
        slot = str(ticket.get("slot_id") or "unknown_slot")
        round_id = str(ticket.get("round_id") or f"history_{idx + 1:04d}")
        qts = str(ticket.get("queried_at") or ticket.get("ts") or "")
        explicit_session = str(ticket.get("round_started_at") or ticket.get("live_ticket_id") or "")
        if explicit_session:
            return ensure(slot, round_id, explicit_session)
        qdt = _parse_ts(qts)
        candidates = [r for r in rows.values() if r.get("slot_id") == slot and r.get("round_id") == round_id]
        best: dict[str, Any] | None = None
        best_delta: float | None = None
        for r in candidates:
            cdt = _parse_ts(r.get("created_at"))
            if qdt and cdt and cdt <= qdt:
                delta = (qdt - cdt).total_seconds()
                if best_delta is None or delta < best_delta:
                    best = r
                    best_delta = delta
        if best is not None:
            return best
        # History can be older than the event tail. Keep it as an independent
        # query session instead of merging it into a later same slot/round.
        return ensure(slot, round_id, qts or str(ticket.get("ticket_query_id") or f"history_{idx}"))

    try:
        lightweight_records = list(store.query_success_records(limit=min(limit, 1000)))
    except Exception:
        lightweight_records = []
    lightweight_by_qid = {str(x.get("ticket_query_id") or ""): x for x in lightweight_records if x.get("ticket_query_id")}

    query_success_records: list[dict[str, Any]] = []
    for idx, ticket in enumerate(history):
        row = row_for_ticket(ticket, idx)
        slot = str(ticket.get("slot_id") or row.get("slot_id") or "unknown_slot")
        round_id = str(ticket.get("round_id") or row.get("round_id") or f"history_{idx + 1:04d}")
        qts = ticket.get("queried_at") or ticket.get("ts") or row.get("updated_at")
        days = ticket.get("normalized_days") or ticket.get("days") or []
        nearest = _nearest_date(days)
        valid_query = bool(ticket.get("valid_query_success")) if "valid_query_success" in ticket else bool(len(days) > 0)
        row["uses_count"] += 1 if valid_query else 0
        if valid_query:
            row["query_success_count"] += 1
        row["days_count"] = len(days)
        row["slots_count"] = len(ticket.get("slots") or [])
        row["matched_count"] = len(ticket.get("matched_slots") or [])
        row["hit_count"] += 1 if ticket.get("target_hit") else 0
        qts_s = str(qts or "")
        prev_qts = str(row.get("last_query_at") or "")
        is_latest_query = (not prev_qts) or (qts_s >= prev_qts)
        if is_latest_query:
            row["last_query_at"] = qts_s
            row["last_query_days_count"] = len(days)
            row["last_nearest_date"] = nearest
            row["last_query_dates_preview"] = list(days)[:8]
            row["last_selected_date"] = str(ticket.get("selected_date") or "")
            if not valid_query:
                row["last_message"] = "官方接口返回空日期：不计为有效查询成功"
        if ticket.get("proxy_display") and row.get("proxy_display") in {"", "-"}:
            row["proxy_display"] = ticket.get("proxy_display")
        if ticket.get("proxy_session") and not row.get("proxy_session"):
            row["proxy_session"] = ticket.get("proxy_session")
        if ticket.get("route") and row.get("route") in {"", "-"}:
            row["route"] = ticket.get("route")
        # updated_at/alive_seconds should represent the session lifecycle end
        # (failure/close/current update) rather than being pulled backwards to
        # the last successful query time.
        if qts_s and (not row.get("updated_at") or qts_s > str(row.get("updated_at"))):
            row["updated_at"] = qts_s
        if not row["created_at"]:
            row["created_at"] = row["updated_at"]
        if not row.get("round_started_at"):
            row["round_started_at"] = row.get("created_at") or ""
        row["query_results"].append({
            "queried_at": qts,
            "post_name": ticket.get("post_name"),
            "days": days,
            "days_count": len(days),
            "nearest_date": nearest,
            "valid_query_success": valid_query,
            "slots": ticket.get("slots") or [],
            "matched_slots": ticket.get("matched_slots") or [],
            "target_hit": bool(ticket.get("target_hit")),
            "clicked_date": ticket.get("clicked_date"),
        })
        if not valid_query:
            continue
        live_round = ticket.get("live_round") or "-"
        ticket_query_id = str(ticket.get("ticket_query_id") or "")
        lightweight_match = lightweight_by_qid.get(ticket_query_id, {})
        seq = ticket.get("seq") or lightweight_match.get("seq")
        steps = ticket.get("steps") if isinstance(ticket.get("steps"), list) else []
        step_names = []
        for step in steps[-8:]:
            if isinstance(step, dict):
                state = step.get("state") if isinstance(step.get("state"), dict) else {}
                step_names.append(str(step.get("name") or step.get("reason") or state.get("stage") or "step"))
        query_success_records.append(
            {
                "query_success_id": _query_success_id(seq, idx + 1),
                "ticket_id": row["ticket_id"],
                "session_id": row["session_id"],
                "session_key": row["key"],
                "session_label": row["session_label"],
                "slot_id": slot,
                "round_id": round_id,
                "live_round": live_round,
                "proxy_display": ticket.get("proxy_display") or row.get("proxy_display") or "-",
                "proxy_session": ticket.get("proxy_session") or row.get("proxy_session") or "",
                "route": ticket.get("route") or row.get("route") or "-",
                "queried_at": qts,
                "query_success_time": qts,
                "interval_since_previous_success_seconds": None,
                "nearest_date": nearest,
                "days_count": len(days),
                "days_preview": list(days)[:10],
                "target_hit": bool(ticket.get("target_hit")),
                "clicked_date": bool(ticket.get("clicked_date")),
                "post_name": ticket.get("post_name") or "",
                "source": ticket.get("query_source") or "business_query",
                "production_flow_summary": " → ".join([x for x in step_names if x][-6:]),
                "ticket_query_id": ticket_query_id,
                "seq": seq,
            }
        )
        row["status"] = "hit" if ticket.get("target_hit") else "success"

    current_slots = store.read_slots()
    pipeline_running = bool((store.pipeline_status() or {}).get("running"))
    for row in rows.values():
        live = current_slots.get(row["slot_id"]) or {}
        live_started = str(live.get("round_started_at") or "")
        is_current = (
            str(live.get("round")) == str(row.get("round_no"))
            and live.get("state") in {"running", "stale"}
            and (not live_started or live_started == str(row.get("round_started_at") or ""))
        )
        if is_current:
            row["status"] = "using" if pipeline_running and live.get("state") == "running" else "paused"
        elif row.get("query_success_count", 0) > 0:
            row["status"] = "hit" if row.get("hit_count") else "success"
        elif row["status"] == "available":
            row["status"] = "failed"
        row["alive_seconds"] = _seconds(row.get("created_at"), row.get("updated_at"))
        row["flow"] = _compact_flow(row.get("raw_flow") or [])
        row["last_result"] = _last_result_zh(row)
        row["status_zh"] = _status_zh(row["status"])

    table = sorted(rows.values(), key=lambda x: x.get("last_query_at") or x.get("updated_at") or x.get("created_at") or "", reverse=True)
    query_success_records.sort(key=lambda x: str(x.get("queried_at") or ""), reverse=False)
    prev_qts: Any = None
    for rec in query_success_records:
        rec["interval_since_previous_success_seconds"] = _seconds(prev_qts, rec.get("queried_at")) if prev_qts else None
        prev_qts = rec.get("queried_at")
    query_success_records.sort(key=lambda x: str(x.get("queried_at") or ""), reverse=True)
    if lightweight_records:
        existing_ids = {str(x.get("ticket_query_id") or x.get("query_success_id") or "") for x in query_success_records}
        for rec in lightweight_records:
            rid = str(rec.get("ticket_query_id") or "")
            if rid and rid in existing_ids:
                continue
            slot = rec.get("slot_id") or ""
            round_id = rec.get("round_id") or ""
            round_started_at = rec.get("round_started_at") or rec.get("queried_at") or rec.get("ts") or ""
            ticket_id = _stable_ticket_id(slot, round_id, round_started_at)
            session_id = _stable_session_id(slot, round_id, round_started_at)
            query_success_records.append(
                {
                    "query_success_id": _query_success_id(rec.get("seq")),
                    "ticket_id": ticket_id,
                    "session_id": session_id,
                    "session_key": f"{slot}/{round_id}/{round_started_at}",
                    "session_label": f"{str(slot or '').replace('slot_', 'Slot ')} / {round_id} / {_short_time_from_ts(round_started_at)}",
                    "slot_id": slot,
                    "round_id": round_id,
                    "live_round": rec.get("live_round") or "",
                    "proxy_display": rec.get("proxy_display") or "-",
                    "proxy_session": rec.get("proxy_session") or "",
                    "route": rec.get("route") or rec.get("route_key") or "-",
                    "queried_at": rec.get("queried_at") or rec.get("ts") or "",
                    "query_success_time": rec.get("queried_at") or rec.get("ts") or "",
                    "interval_since_previous_success_seconds": None,
                    "nearest_date": rec.get("nearest_date") or "",
                    "days_count": rec.get("days_count") or 0,
                    "days_preview": rec.get("days_preview") or [],
                    "target_hit": bool(rec.get("target_hit")),
                    "clicked_date": bool(rec.get("clicked_date")),
                    "post_name": rec.get("post_name") or "",
                    "source": "query_success_records",
                    "production_flow_summary": rec.get("flow_summary") or "",
                    "ticket_query_id": rid,
                    "seq": rec.get("seq"),
                }
            )
        query_success_records.sort(key=lambda x: str(x.get("queried_at") or ""), reverse=False)
        prev_qts = None
        for rec in query_success_records:
            rec["interval_since_previous_success_seconds"] = _seconds(prev_qts, rec.get("queried_at")) if prev_qts else None
            prev_qts = rec.get("queried_at")
        query_success_records.sort(key=lambda x: str(x.get("queried_at") or ""), reverse=True)
    total = len(table)
    available = sum(1 for r in table if r["status"] in {"using", "success", "paused", "hit"})
    used = sum(1 for r in table if r["query_success_count"] > 0)
    failed = sum(1 for r in table if r["status"] == "failed")
    hit = sum(r["hit_count"] for r in table)
    query_success = sum(r["query_success_count"] for r in table)
    reusable = sum(1 for r in table if r["uses_count"] >= 2)

    stage_names = sorted({k for r in table for k in r.get("stage_durations", {})})
    avg_stage_durations = {name: round(mean([r["stage_durations"].get(name, 0) for r in table if r["stage_durations"].get(name, 0)]), 2) for name in stage_names}

    by_proxy: dict[str, dict[str, Any]] = {}
    for r in table:
        proxy = r.get("proxy_display") or "-"
        rec = by_proxy.setdefault(proxy, {"proxy_display": proxy, "total": 0, "query_success": 0, "failed": 0, "hit": 0, "avg_alive_seconds": 0, "alive_samples": []})
        rec["total"] += 1
        rec["query_success"] += 1 if r.get("query_success_count") else 0
        rec["failed"] += 1 if r.get("status") == "failed" else 0
        rec["hit"] += r.get("hit_count") or 0
        if r.get("alive_seconds"):
            rec["alive_samples"].append(r["alive_seconds"])
    for rec in by_proxy.values():
        rec["success_rate"] = round(rec["query_success"] / rec["total"] * 100, 1) if rec["total"] else 0
        rec["avg_alive_seconds"] = round(mean(rec.pop("alive_samples") or [0]), 1)

    hourly = [{"hour": f"{i:02d}:00", "queries": 0, "hits": 0, "new_dates": 0} for i in range(24)]
    seen_dates: set[str] = set()
    release_events = []
    for ticket in sorted(history, key=lambda x: str(x.get("queried_at") or x.get("ts") or "")):
        dt = _parse_ts(ticket.get("queried_at") or ticket.get("ts"))
        if not dt:
            continue
        h = dt.hour
        hourly[h]["queries"] += 1
        hourly[h]["hits"] += 1 if ticket.get("target_hit") else 0
        dates = set(ticket.get("days") or [])
        new_dates = sorted(dates - seen_dates)
        if new_dates:
            hourly[h]["new_dates"] += len(new_dates)
            release_events.append({"queried_at": ticket.get("queried_at") or ticket.get("ts"), "new_dates": new_dates, "days_count": len(dates), "slot_id": ticket.get("slot_id"), "round_id": ticket.get("round_id")})
        seen_dates |= dates

    smart = SmartQueryGate(store, store.config).snapshot() if getattr(store, "config", None) is not None else {}
    active_slots_count = sum(1 for x in current_slots.values() if isinstance(x, dict) and x.get("state") == "running")
    sla = SlaOrchestrator(store, store.config).snapshot(active_slots=active_slots_count) if getattr(store, "config", None) is not None else {}
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "total_sessions": total,
            "available": available,
            "used": used,
            "failed": failed,
            "hit": hit,
            "query_success": query_success,
            "success_rate": round(query_success / max(1, total) * 100, 1),
            "reuse_probability": round(reusable / max(1, used) * 100, 1),
            "avg_alive_seconds": round(mean([r["alive_seconds"] for r in table if r["alive_seconds"]] or [0]), 1),
            "avg_stage_durations": avg_stage_durations,
            "recovery_events": sum(int(r.get("recovery_count") or 0) for r in table),
            "smart_scheduler": smart,
            "sla_orchestrator": sla,
        },
        "smart_scheduler": smart,
        "sla_orchestrator": sla,
        "sessions": table,
        "query_success_records": query_success_records,
        "by_proxy": sorted(by_proxy.values(), key=lambda x: (x["success_rate"], x["query_success"]), reverse=True),
        "hourly": hourly,
        "release_events": release_events[-50:],
    }


@router.get("/latest")
def latest_ticket():
    store = container()["store"]
    return {
        "latest": store.latest_ticket(),
        "history": store.ticket_history(200),
        "query_count_today": store.ticket_query_count_today(),
        "availability_text": store.availability_text(),
        "booking_signal": store.booking_signal(),
    }


@router.get("/analytics")
def ticket_analytics(limit: int = Query(3000, ge=100, le=10000)):
    store = container()["store"]
    return build_ticket_analytics(store, limit=limit)


@router.get("/query-success-records")
def query_success_records(
    limit: int = Query(100, ge=1, le=1000),
    after_seq: int = Query(0, ge=0),
):
    store = container()["store"]
    rows = store.query_success_records(limit=limit, after_seq=after_seq)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "after_seq": after_seq,
        "count": len(rows),
        "last_seq": max([int(r.get("seq") or 0) for r in rows] or [after_seq]),
        "records": rows,
    }

