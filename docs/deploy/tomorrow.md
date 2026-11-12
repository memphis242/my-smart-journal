# Your steps tomorrow

In order, with rough times. Each line points at the runbook section that has
the exact commands and the check that must pass. **[YOU]** = console clicks,
account actions or passphrases. **[PC]** / **[BOX]** = commands.

Budget about **3 hours**, plus up to a day of waiting if the domain or the B2
account is not ready yet.

## Before the day (or first thing), about 60 min

| # | Who | What | Time | Runbook |
|---|---|---|---|---|
| 0a | YOU | Decide region (one with NVMe Block Storage), hostname `journal.<your domain>` | 5 min | [3, Decisions](RUNBOOK.md#decisions) |
| 0b | YOU | Domain on Cloudflare; API token "Edit zone DNS" for that one zone | 15 min (+ nameserver wait) | [3, Accounts](RUNBOOK.md#accounts-and-credentials) item 3 |
| 0c | YOU | B2: 2FA, private bucket, lifecycle "keep prior versions 30 days", bucket-only read/write key | 10 min | item 4 |
| 0d | YOU | Healthchecks.io: five checks, periods and graces from the table | 10 min | item 5 |
| 0e | YOU | NetBird: account with MFA, peer approval on, your PC joined | 10 min | item 2 |
| 0f | YOU | RunPod: a dedicated API key for the box | 5 min | item 6 |
| 0g | YOU | Password manager: create the entries; generate the LUKS passphrase (write it on paper twice) and the restic password | 10 min | item 7 |

## The deployment, about 2 hours

| # | Who | What | Time | Runbook |
|---|---|---|---|---|
| 1 | YOU | Vultr: 2FA, API off, maintenance emails | 5 min | [Step 1](RUNBOOK.md#step-1-vultr-account-security-you) |
| 2 | YOU | Vultr: add your SSH public key | 2 min | [Step 2](RUNBOOK.md#step-2-register-your-ssh-key-on-vultr-you) |
| 3 | YOU | Vultr: firewall group `msj` with the one inert rule | 5 min | [Step 3](RUNBOOK.md#step-3-create-the-firewall-group-you) |
| 4 | PC | `deploy/scripts/build-release.sh` | 2 min | [Step 4](RUNBOOK.md#step-4-build-the-release-tarball-pc) |
| 5 | YOU | NetBird one-off setup key; paste it into a copy of `deploy/cloud-init.yaml` | 5 min | [Step 5](RUNBOOK.md#step-5-create-the-servers-netbird-setup-key-and-the-user-data-you-pc) |
| 6 | YOU | Deploy the instance (AlmaLinux 10, `vhp-2c-4gb-amd`, no backups/IPv6/VPC, user-data, firewall group); add and attach 20 GB NVMe Block Storage | 15 min | [Step 6](RUNBOOK.md#step-6-deploy-the-instance-and-its-volume-you) |
| 7 | YOU, PC | Approve the peer, revoke the key, SSH over NetBird, prove the public IP is closed | 10 min | [Step 7](RUNBOOK.md#step-7-approve-the-peer-reach-the-box-over-netbird-close-the-loop-you-pc) |
| 8 | BOX | Copy the release, `bootstrap.sh host`, new root password, `dnf upgrade`, reboot if needed | 15 min | [Step 8](RUNBOOK.md#step-8-install-the-host-box-pc) |
| 9 | YOU, BOX | LUKS format (passphrase), header backup off the box, `host.env`, `bootstrap.sh volume`, prove the paper copy | 15 min | [Step 9](RUNBOOK.md#step-9-encrypt-the-data-volume-you-box) |
| 10 | YOU | Cloudflare A record to the NetBird IP, DNS only, TTL 5 min; `dig` from the PC | 5 min | [Step 10](RUNBOOK.md#step-10-point-the-hostname-at-the-box-you-pc) |
| 11 | BOX | Secrets (`app.env`, `caddy.env`, `restic-password`, `backup.env`, `healthchecks.env`); `restic init` | 10 min | [Step 11](RUNBOOK.md#step-11-write-the-secrets-box) |
| 12 | BOX | `msj-release install`; `msj-cli create-token` (store it); HTTPS health from the PC | 10 min | [Step 12](RUNBOOK.md#step-12-install-the-journal-box) |
| 13 | BOX | Run backup, verify, drill, cert check and health by hand; all five green | 10 min | [Step 13](RUNBOOK.md#step-13-prove-every-scheduled-job-once-by-hand-box-you) |
| 14 | YOU | Sign in from the PC; NetBird + journal app on Android | 10 min | [Step 14](RUNBOOK.md#step-14-connect-the-clients-you) |
| 15 | YOU, BOX | Reboot rehearsal: `msj-lock && reboot`, `msj-unlock`; log in once on the Vultr console | 10 min | [Step 15](RUNBOOK.md#step-15-rehearse-a-reboot-you-box) |

## After

- Anything that surprised you goes into RUNBOOK.md at its step, today
  ([section 1](RUNBOOK.md#1-keeping-this-runbook-true)).
- Calendar: the domain's expiry, a month ahead.
- Bookmark [5. Unlock after a reboot](RUNBOOK.md#5-unlock-after-a-reboot) and
  [6. Deploying a new version](RUNBOOK.md#6-deploying-a-new-version).

## If something fails

Every script names what failed, why, and the next command to run. Start from
its message. The usual suspects for each step:

| Step | Usual cause |
|---|---|
| 6 | No peer appears: the setup key was used or expired. Fix it from the Vultr console. |
| 7 | `nc` says "refused": a firewall layer is missing. Recheck step 3 and `firewall-cmd --get-active-zones`. |
| 12 | No certificate: the token is on the wrong zone, the record is proxied, or `MSJ_HOSTNAME` has a typo. |
| 13 | The backup fails: a typo in the B2 endpoint or key in `backup.env`. |
