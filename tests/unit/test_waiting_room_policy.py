from importlib import import_module

WaitingRoomPolicy = import_module("05_stage_components.stage02_waiting_room.policy").WaitingRoomPolicy


def test_direct_only_slot_is_killed():
    policy = WaitingRoomPolicy({"slot_02"}, 1)
    decision = policy.decide_on_enter("slot_02", [])
    assert decision.kill_now is True
    assert decision.reason == "direct_only_waiting_room"


def test_waiting_slot_can_hold_one_lane():
    policy = WaitingRoomPolicy({"slot_02"}, 1)
    decision = policy.decide_on_enter("slot_01", [])
    assert decision.allow_wait is True


def test_extra_waiting_slot_is_killed():
    policy = WaitingRoomPolicy({"slot_02"}, 1)
    decision = policy.decide_on_enter("slot_03", ["slot_01/round_0001"])
    assert decision.kill_now is True
    assert decision.reason == "waiting_room_slots_full"


def test_peak_window_allows_only_short_single_probe():
    policy = WaitingRoomPolicy(set(), 3)
    first = policy.decide_on_enter("slot_01", [], active_slots=10, hot_query_sessions=0, seconds_since_success=300, peak_mode="peak")
    assert first.allow_wait is True
    assert first.max_wait_seconds == 10
    assert first.max_waiting_slots == 1
    second = policy.decide_on_enter("slot_02", ["slot_01/round_0001"], active_slots=10, hot_query_sessions=0, seconds_since_success=300, peak_mode="peak")
    assert second.kill_now is True


def test_hot_low_normal_window_waits_briefly_not_three_minutes():
    policy = WaitingRoomPolicy(set(), 3)
    decision = policy.decide_on_enter("slot_01", [], active_slots=10, hot_query_sessions=0, seconds_since_success=80, peak_mode="normal")
    assert decision.allow_wait is True
    assert decision.max_wait_seconds == 30
    assert decision.max_waiting_slots == 1


def test_loose_hot_pool_caps_waiting_to_two_and_three_minutes():
    policy = WaitingRoomPolicy(set(), 5)
    decision = policy.decide_on_enter("slot_01", ["slot_x/round"], active_slots=10, hot_query_sessions=4, seconds_since_success=20, peak_mode="normal")
    assert decision.allow_wait is True
    assert decision.max_waiting_slots == 2
    assert decision.max_wait_seconds == 180
