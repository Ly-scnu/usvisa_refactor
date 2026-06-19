import asyncio
from importlib import import_module
from types import SimpleNamespace


page_classifier = import_module("03_browser_management.page_classifier")
failure_classifier = import_module("00_infrastructure.orchestration.session_health.failure_classifier")
cooldown_policy = import_module("00_infrastructure.orchestration.query_dispatcher.cooldown_policy")
session_health = import_module("00_infrastructure.orchestration.session_health.classifier")
protocol = import_module("05_stage_components.stage04_query.protocol")
query_gate = import_module("00_infrastructure.orchestration.query_gate")


class _FakeLocator:
    def __init__(self, text: str = "", count: int = 0):
        self._text = text
        self._count = count

    async def inner_text(self, timeout: int = 0) -> str:
        return self._text

    async def count(self) -> int:
        return self._count


class _FakePage:
    def __init__(self, *, url: str, title: str, body: str):
        self.url = url
        self._title = title
        self._body = body

    async def title(self) -> str:
        return self._title

    def locator(self, selector: str) -> _FakeLocator:
        if selector == "body":
            return _FakeLocator(self._body, 1)
        return _FakeLocator("", 0)

    async def evaluate(self, script: str) -> str:
        return ""


def test_b2c_callback_page_not_found_is_not_idp_loading():
    page = _FakePage(
        url="https://www.usvisascheduling.com/en-US/signin-aad-b2c_1",
        title="Page Not Found · Customer Self-Service",
        body="Page Not Found",
    )
    state = asyncio.run(page_classifier.classify_page(page))
    assert state.stage == "callback_not_found"
    assert state.reason == "b2c_callback_page_not_found"


def test_failure_classifier_marks_page_view_blocked_as_recoverable_handoff():
    result = SimpleNamespace(message="page view blocked", payload={"needs_recover": "auth_or_cf"})
    fc = failure_classifier.FailureClassifier().classify(result)
    assert fc.kind == "auth_or_cf_block"
    assert fc.severity == "recoverable"
    assert fc.handoff is True


def test_bad_session_failure_gets_real_cooldown_even_in_release_mode():
    cfg = SimpleNamespace(
        smart_orchestrator=SimpleNamespace(
            release_bad_session_cooldown_seconds=30.0,
            bad_session_cooldown_seconds=90.0,
        )
    )

    class Store:
        def sla_state(self):
            return {}

    policy = cooldown_policy.DynamicCooldownPolicy(cfg, Store())
    decision = policy.after_failure_kind(failure_kind="page view blocked auth_or_cf")
    assert decision.mode == "page_view_blocked_failure"
    assert decision.cooldown_seconds >= 60.0


def test_rate_limited_failure_uses_longer_specific_cooldown():
    cfg = SimpleNamespace(
        smart_orchestrator=SimpleNamespace(
            rate_limited_cooldown_seconds=120.0,
            release_rate_limited_cooldown_seconds=120.0,
        )
    )

    class Store:
        def sla_state(self):
            return {}

    decision = cooldown_policy.DynamicCooldownPolicy(cfg, Store()).after_failure_kind(failure_kind="page view failed: rate_limited")
    assert decision.mode == "rate_limited_failure"
    assert decision.cooldown_seconds >= 120.0


def test_transport_failed_status_reason():
    rec = {"status": 0, "error": "AbortError: business_fetch_timeout", "timeout_ms": 15000}
    assert protocol.transport_failed(rec) is True
    assert protocol.status_reason(rec) == "failed_to_fetch"


def test_callback_not_found_is_terminal_risk_for_query_gate_health():
    h = session_health.SessionHealthClassifier().classify(
        {
            "slot": "slot_01",
            "state": "running",
            "stage": "business_query",
            "live_page_stage": "callback_not_found",
        },
        {"state": "ready_warm", "success_count": 1},
    )
    assert h.state == "terminal_risk"
    assert h.gate_allowed is False


def test_ready_hot_session_requires_live_ready_page_not_false_hot_pool():
    gate = query_gate.SmartQueryGate(None, SimpleNamespace(smart_orchestrator=SimpleNamespace(enabled=True)))
    state = {
        "sessions": {
            "hot": {
                "session_key": "hot",
                "slot_id": "slot_01",
                "state": "ready_warm",
                "success_count": 2,
                "next_query_at": "",
                "last_cooldown_mode": "success",
            }
        }
    }
    false_hot_slots = {
        "slot_01": {
            "slot": "slot_01",
            "state": "running",
            "stage": "business_query",
            "live_page_stage": "cf_challenge",
            "round_started_at": "",
        }
    }
    assert gate._ready_hot_session_exists(state, false_hot_slots) is False

    real_hot_slots = {
        "slot_01": {
            "slot": "slot_01",
            "state": "running",
            "stage": "business_query",
            "live_page_stage": "home",
            "round_started_at": "",
        }
    }
    assert gate._ready_hot_session_exists(state, real_hot_slots) is True


def test_ready_hot_session_rejects_stale_session_key_from_old_round():
    gate = query_gate.SmartQueryGate(None, SimpleNamespace(smart_orchestrator=SimpleNamespace(enabled=True)))
    state = {
        "sessions": {
            "slot_07/round_old/2026-06-16T06:00:00+08:00": {
                "session_key": "slot_07/round_old/2026-06-16T06:00:00+08:00",
                "slot_id": "slot_07",
                "state": "ready_warm",
                "success_count": 1,
                "next_query_at": "",
                "last_cooldown_mode": "success",
            },
            "slot_07/round_current/2026-06-17T21:47:28+08:00": {
                "session_key": "slot_07/round_current/2026-06-17T21:47:28+08:00",
                "slot_id": "slot_07",
                "state": "ready_warm",
                "success_count": 1,
                "next_query_at": "",
                "last_cooldown_mode": "success",
            },
        }
    }
    slots = {
        "slot_07": {
            "slot": "slot_07",
            "state": "running",
            "stage": "business_query",
            "live_page_stage": "home",
            "round_started_at": "2026-06-17T21:47:28+08:00",
        }
    }
    candidates = gate._ready_hot_session_candidates(state, slots)
    assert [x["session_key"] for x in candidates] == ["slot_07/round_current/2026-06-17T21:47:28+08:00"]
