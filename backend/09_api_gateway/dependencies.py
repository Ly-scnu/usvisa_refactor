from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from pathlib import Path

SystemStatusService = import_module("06_business_services.system_status").SystemStatusService
load_config = import_module("00_infrastructure.config.loader").load_config
Database = import_module("00_infrastructure.database.db").Database
EventBus = import_module("00_infrastructure.events.event_bus").EventBus
StateStore = import_module("00_infrastructure.runtime.state_store").StateStore
ProducerService = import_module("07_scheduler.producer_service").ProducerService


@lru_cache(maxsize=1)
def container():
    cfg = load_config(Path(__file__).resolve().parents[2])
    db = Database(cfg.data_dir / "database" / "app.db")
    event_bus = EventBus(db, cfg.data_dir / "logs" / "events.jsonl")
    store = StateStore(cfg)
    producer = ProducerService(cfg, event_bus, store)
    status = SystemStatusService(cfg, store, producer, event_bus)
    return {"config": cfg, "db": db, "event_bus": event_bus, "store": store, "producer": producer, "status": status}
