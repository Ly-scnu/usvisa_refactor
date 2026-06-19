from pathlib import Path
from importlib import import_module

load_config = import_module("00_infrastructure.config.loader").load_config


def test_load_config():
    cfg = load_config(Path(__file__).resolve().parents[2])
    assert cfg.slots.total_slots == 10
    assert cfg.slots.waiting_room_slots == 10
    assert cfg.smart_orchestrator.min_slots == 10
    assert cfg.smart_orchestrator.max_slots == 10
    assert cfg.smart_orchestrator.normal_active_slots == 10
    assert cfg.smart_orchestrator.drain_enabled is False
    assert cfg.slots.direct_only_slots == []
    assert cfg.producer.protocol_direct_from_home is True
    assert cfg.producer.protocol_only_post_selection is True
    assert cfg.booking.armed is False  # sanitized GitHub package defaults to safe/no-submit mode

