# OpenSands US Visa Refactor

Standalone rewrite with project-local runtime, API, UI, event store, and producer.

## Quick start

```powershell
cd D:\OpenSands\sites\www.usvisascheduling.com\test\usvisa_refactor
$env:PYTHONPATH = "$PWD\backend"
python .\backend\run_api.py
```

API: <http://127.0.0.1:18890/docs>

Frontend:

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 18891
```

## Current milestone

- New layered backend, API, SQLite events, JSONL logs.
- New Vue dashboard structure.
- Producer is project-local under `backend/07_scheduler`; runtime state is stored in `storage/runtime`.
- Slot policy: slot_01 waiting room max 180s; slot_02 direct-only.


