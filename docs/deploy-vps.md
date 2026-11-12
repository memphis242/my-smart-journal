# Deploying on a VPS

The deployment procedure lives in [deploy/RUNBOOK.md](deploy/RUNBOOK.md). It
covers a Vultr instance reachable only over NetBird, with a LUKS-encrypted
data volume, Caddy with a DNS-01 certificate, and restic backups to B2. For
the order of work on deployment day, see [deploy/tomorrow.md](deploy/tomorrow.md).

The files it uses are in [`deploy/`](../deploy/README.md).

Two notes for readers who arrive here from older links:

- **Token re-issue.** Migration 009 requires re-issuing legacy bearer tokens
  with `msj create-token`. On the VPS, run `msj-cli create-token` (runbook
  section 8).
- **Offline model.** The services run with `HF_HUB_OFFLINE=1`. The model is
  downloaded once, at its pinned revision, by `msj-release install` (runbook
  step 12).
