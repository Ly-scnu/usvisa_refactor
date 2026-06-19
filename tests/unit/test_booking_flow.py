import asyncio
from importlib import import_module
from types import SimpleNamespace


models = import_module("05_stage_components.stage04_query.models")
slot_collector = import_module("05_stage_components.stage04_query.slot_collector")
submitter = import_module("05_stage_components.stage05_booking.submitter")


def _cfg(tmp_path):
    return SimpleNamespace(
        project_root=tmp_path,
        target=SimpleNamespace(
            start_date="",
            cutoff_date="2026-06-22",
            end_date="2026-06-22",
            is_reschedule=False,
            lang="zh-CN",
        ),
        booking=SimpleNamespace(
            armed=True,
            max_parallel_submit=3,
            parallel_submit_delays_ms=[0, 1, 2],
            success_latch=True,
        ),
    )


def test_accepted_date_builds_booking_signal_and_submit_fires_parallel(monkeypatch, tmp_path):
    async def fake_entries_fetch(page, method, url, parameters, *, referrer, timeout_ms=15000):
        assert "get-family-consular-schedule-entries" in url
        assert parameters["Date"] == "2026-06-22T00:00:00.000Z"
        return (
            {"status": 200, "text_len": 200},
            {
                "Token": "submit-token",
                "ScheduleEntries": [
                    {"ID": None, "EntriesAvailable": 3, "Time": "08:00", "Num": 1},
                    {"ID": None, "EntriesAvailable": 0, "Time": "08:15", "Num": 2},
                ],
            },
        )

    monkeypatch.setattr(slot_collector, "browser_fetch", fake_entries_fetch)

    ctx = SimpleNamespace(
        page=object(),
        runtime_config=_cfg(tmp_path),
        slot_id="slot_test",
        round_id="round_test",
        event_bus=None,
        store=None,
        booking_signal=None,
    )
    schedule = models.ScheduleContext(
        app_id="app-1",
        applications=["app-1"],
        referrer="https://www.usvisascheduling.com/zh-CN/schedule/",
    )
    post = models.PostSelection(post_id="beijing-post", post_name="BEIJING (北京)", source="alias")
    dates = models.DateCollection(days=["2026-06-22T00:00:00.000Z"], token="days-token")
    decision = models.DateDecision(acceptable_dates=["2026-06-22"], rejected_dates=[], selected_date="2026-06-22", target_hit=True)
    steps = []

    err, slots = asyncio.run(slot_collector.collect_slots_for_accepted_date(ctx, schedule, post, dates, decision, steps))
    assert err is None
    assert slots.selected["date"] == "2026-06-22"
    assert ctx.booking_signal["token"] == "submit-token"
    assert ctx.booking_signal["submit_route"] == "/api/v1/schedule-group/schedule-consular-appointments-for-family"

    submit_calls = []

    async def fake_submit_fetch(page, method, url, parameters, *, referrer, timeout_ms=15000):
        submit_calls.append((url, dict(parameters)))
        return ({"status": 200, "text_len": 100}, {"AllScheduled": True, "HasError": False})

    monkeypatch.setattr(submitter, "browser_fetch", fake_submit_fetch)
    ctx.project_root = tmp_path
    result = asyncio.run(submitter.BookingSubmitStage().execute(ctx))

    assert result.ok is True
    assert len(submit_calls) == 3
    assert all("schedule-consular-appointments-for-family" in call[0] for call in submit_calls)
    assert submit_calls[0][1]["Date"] == "2026-06-22T00:00:00.000Z"
    assert submit_calls[0][1]["Num"] == "1"
    assert submit_calls[0][1]["Token"] == "submit-token"
    assert submit_calls[0][1]["isReschedule"] == "false"


def test_submit_classifier_does_not_treat_cf_1015_html_as_success():
    assert submitter.classify_submit_result(None, {"status": 200, "text": "<html>Error 1015 you are being rate limited</html>"}) == "ban_1015"
    assert submitter.classify_submit_result(None, {"status": 200, "text": "<html>Just a moment... challenges.cloudflare.com</html>"}) == "auth_or_cf_block"
    assert submitter.classify_submit_result({"AllScheduled": True}, {"status": 200, "text": ""}) == "success"
