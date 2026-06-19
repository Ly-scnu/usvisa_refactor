from importlib import import_module
from types import SimpleNamespace

q = import_module("05_stage_components.stage04_query.query")


def cfg():
    return SimpleNamespace(target=SimpleNamespace(post_name="BEIJING", post_aliases=["北京"], post_id="", start_date="", cutoff_date="2026-06-22", end_date="2026-06-22"))


def test_target_window():
    assert q.in_target_window("2026-06-21T00:00:00.000Z", cfg()) is True
    assert q.in_target_window("2026-06-23", cfg()) is False


def test_choose_post_alias():
    post_id, name, source, clean = q.choose_post([{"ID": "p1", "Name": "BEIJING"}], cfg())
    assert post_id == "p1"
    assert source == "alias"


def test_entry_available():
    assert q.entry_available({"EntriesAvailable": 1}) is True
    assert q.entry_available({"EntriesAvailable": 0}) is False

