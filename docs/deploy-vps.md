# Deploying on a VPS

Use Python 3.14, Caddy, systemd, and a dedicated unprivileged `msj` account.
Keep journal data and configuration outside the checkout. The examples use
`/opt/msj/venv`, `/etc/msj`, and `/var/lib/msj`; create them with ownership
appropriate to the service account. Only that account should read configuration
containing provider credentials and journal data.

Build a wheel from `backend/` and install it in `/opt/msj/venv`. Install the
`semantic` extra for local sentence-transformers inference, or set
`[embedding] provider = "none"` to disable it. The default CPU model is
`BAAI/bge-small-en-v1.5`; its first load downloads model weights. Set
`HF_HOME=/var/lib/msj/models` for a persistent, service-writable model cache.
Copy `backend/config.example.toml` to `/etc/msj/config.toml` and configure actual
provider endpoints before processing private entries.

Create `/etc/msj/service.env` (owner root, group msj, mode 0640):

```ini
MSJ_CONFIG_FILE=/etc/msj/config.toml
MSJ_DATA_DIR=/var/lib/msj
MSJ_WEB_DIR=/opt/msj/web
HF_HOME=/var/lib/msj/models
```

Add `RUNPOD_API_KEY` or `OPENAI_API_KEY` only when the selected routes need it.
Never put credentials in a unit file, source control, or a publicly served
directory. Run `msj migrate` and `msj create-token` as the service user with the
same environment. Store the displayed token in a password manager. Creating a
replacement token revokes existing browser sessions.

Build the web client following its own build instructions and place the built
files (including `index.html`) in `/opt/msj/web`. Set `MSJ_WEB_DIR` to this
directory. The API service serves these files and handles SPA routes. Leave
`MSJ_INSECURE_COOKIES` unset behind TLS. For a local loopback development server
only, set it to `1` to allow HTTP cookies.

Install the two units from `deploy/` into `/etc/systemd/system/`, review their
paths, then run `systemctl daemon-reload` and
`systemctl enable --now msj-serve msj-worker`. Stop both services before upgrading
the package, migrate using the new package, and restart both. The worker and
manual HTTP/CLI batches share a SQLite lease, so overlapping triggers do not
duplicate processing. Check `journalctl -u msj-serve -u msj-worker` and authenticated
`GET /api/v1/queue` for service health and processing failures.

Point the journal DNS name at the VPS, permit inbound ports 80 and 443, and
install the example `deploy/Caddyfile` with your hostname. Caddy manages TLS and
serves HTTP/2. Port 8765 stays bound to loopback. The example request limit is
52,445,184 bytes, matching the default 50 MiB upload limit plus 16 KiB metadata;
change both limits together when increasing `upload_limit_bytes`. Do not enable
request-body or credential logging in the proxy.

Back up `journal.db` using SQLite's backup API (or stop both services and copy
the database together with any WAL/SHM files) and copy the `audio/` directory in
the same maintenance window. Include private configuration in an encrypted
backup and restrict access to backups as strictly as the live journal. Model
cache files can be downloaded again and need not be backed up. Periodically
restore a backup into a separate private data directory and run `msj migrate`
to confirm it is usable. The MVP does not encrypt journal data at rest; use
encrypted disks and encrypted off-host backups where needed.
