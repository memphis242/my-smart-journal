# deploy/

Deployment files for the Vultr VPS. The procedure that uses them is
[docs/deploy/RUNBOOK.md](../docs/deploy/RUNBOOK.md). Nothing here holds a
secret or a real hostname; those live only on the box.

| Path | What it is | Installed to |
|---|---|---|
| `cloud-init.yaml` | First-boot user-data: NetBird enrolment with a one-off setup key, firewalld `drop`, keys-only sshd | Vultr deploy form |
| `scripts/build-release.sh` | **Workstation:** builds the release tarball (wheel, web bundle, Caddy with the DNS module, this directory) | not installed |
| `scripts/bootstrap.sh` | Idempotent host setup (`host`) and encrypted-volume layout (`volume`) | run from the unpacked release |
| `scripts/msj-unlock`, `msj-lock` | Unlock the LUKS volume and start the journal; stop and lock it | `/usr/local/sbin/` |
| `scripts/msj-release` | Install, activate and roll back releases | `/usr/local/sbin/` |
| `scripts/msj-cli` | Run `msj` (e.g. `create-token`) as the service account with the service environment | `/usr/local/sbin/` |
| `scripts/msj-backup`, `msj-backup-verify`, `msj-restore-drill` | Nightly backup, weekly prune and check, monthly restore drill | `/usr/local/sbin/` |
| `scripts/msj-health`, `msj-cert-check` | Dead-man switches for the service and the certificate | `/usr/local/sbin/` |
| `scripts/lib/common.sh` | Shared layout and helpers | `/usr/local/lib/msj/` |
| `systemd/msj.target` | Caddy + API + worker; started only by `msj-unlock` | `/etc/systemd/system/` |
| `systemd/msj-serve.service`, `msj-worker.service`, `msj-migrate.service` | The app, sandboxed | `/etc/systemd/system/` |
| `systemd/caddy.service` | Caddy on the NetBird address, sandboxed | `/etc/systemd/system/` |
| `systemd/msj-*.timer` + `.service` | Backup, verify, drill, certificate and health jobs | `/etc/systemd/system/` |
| `caddy/Caddyfile` | TLS via DNS-01, reverse proxy to loopback; values from the environment | `/etc/caddy/Caddyfile` |
| `config.production.toml` | The app configuration with the RunPod endpoints | `/etc/msj/config.toml` (first install only) |
| `env/msj.env` | Non-secret service environment | `/etc/msj/msj.env` |
| `env/*.example` | Templates for `host.env`, `healthchecks.env` and the secrets | by hand, per the runbook |

Checks to run after editing:

- Scripts: `shellcheck -x -P deploy/scripts/lib deploy/scripts/*.sh deploy/scripts/msj-*`
  (style level is clean).
- Units: `systemd-analyze verify` on AlmaLinux 10.
- Caddyfile: `caddy validate` with the module built in.
