from importlib import import_module

Database = import_module("00_infrastructure.database.db").Database
_event_mod = import_module("00_infrastructure.events.event_bus")
Event = _event_mod.Event
EventBus = _event_mod.EventBus


def test_event_bus_publish(tmp_path):
    db = Database(tmp_path / "app.db")
    bus = EventBus(db, tmp_path / "events.jsonl")
    bus.publish(Event("unit_test", payload={"ok": True}))
    assert bus.tail(1)[0]["event_type"] == "unit_test"
