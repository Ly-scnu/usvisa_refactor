# Architecture

The refactor uses a stable external adapter first, then migrates internals stage by stage.

```text
Vue Dashboard -> FastAPI Gateway -> Business Services -> Legacy Adapter -> producer_daemon.py
                                -> Event Bus -> SQLite + events.jsonl
                                -> Status Reader -> legacy status/ticket files
```

Layer mapping follows `重构v1/说明.md` but uses English names to avoid import and Windows path bugs.
