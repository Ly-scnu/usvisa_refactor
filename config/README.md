# Config usage

Only `*.example.toml` should be committed.

Initialize local private configs:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_config.ps1
```

Then edit:

- `config/app.toml` — target, scheduling and booking switches.
- `config/accounts.toml` — your own account credentials/security answers.
- `config/proxy.toml` — your own proxy provider credentials/routes.

Never commit real `*.toml`, cookies, browser profiles, screenshots, logs or runtime DB files.
