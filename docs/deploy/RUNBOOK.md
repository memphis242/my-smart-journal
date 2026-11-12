# Deployment runbook: my-smart-journal on a Vultr VPS

This runbook puts the journal on one Vultr instance that nobody on the public
internet can reach. You administer it, and your PC and Android phone use it,
only over the NetBird mesh VPN. It is a private, single-user service:

- a FastAPI API that also serves the web UI;
- a batch worker, the only process that calls RunPod Serverless;
- SQLite in WAL mode;
- a small CPU sentence-transformer (bge-small-en-v1.5) for semantic search.

All of the journal's data lives on a LUKS-encrypted volume that is unlocked by
hand after every reboot.

For the order of work on deployment day, with time estimates, see
[tomorrow.md](tomorrow.md). This file is the reference that list points into.

**Markers used on every step**

| Marker | Meaning |
|---|---|
| **[YOU]** | A web console click, an account action, or a passphrase only you can type. |
| **[PC]** | A command on your workstation, in the repository checkout. |
| **[BOX]** | A command on the VPS as root, over `ssh root@msj-host.netbird.cloud`. |
| **Verify** | A check that must pass before you move on. |

Placeholders: `journal.example.com` is your journal's hostname, `100.x.y.z` is
the box's NetBird address, `203.0.113.10` is its public IPv4. Never commit
real values. This repository is public.

## Contents

1. [Keeping this runbook true](#1-keeping-this-runbook-true)
2. [What you are building](#2-what-you-are-building)
3. [Before you start](#3-before-you-start)
4. [Provisioning, step by step](#4-provisioning-step-by-step)
5. [Unlock after a reboot](#5-unlock-after-a-reboot)
6. [Deploying a new version](#6-deploying-a-new-version)
7. [Backups and restore](#7-backups-and-restore)
8. [Secrets and configuration](#8-secrets-and-configuration)
9. [Observability](#9-observability)
10. [What will bite](#10-what-will-bite)
11. [What carried over from the earlier DigitalOcean runbook, and what was dropped](#11-what-carried-over-from-the-earlier-digitalocean-runbook-and-what-was-dropped)
12. [Sources](#12-sources)

## 1. Keeping this runbook true

This document is only worth as much as it is true. The best corrections it
will ever get come from the first real deployment. The rule is the same one
the earlier DigitalOcean runbook lives by: **anything a real deployment teaches that this
file does not already say goes into this file, at the step it belongs to, in
the same sitting.** That includes:

- a field the Vultr form asked for and this file did not mention;
- a default that differed from what is written here;
- a charge or hold you did not expect;
- a command that failed, and what fixed it;
- a decision made on the spot, with its reason.

Write it at the step, not in an appendix, and write how things *are*, not what
changed. Git holds the history.

A one-off shortcut (a skipped backup, a smaller plan for a test) is a
deviation. Write it down as a deviation, or not at all; never fold it into the
procedure.

## 2. What you are building

```
 PC browser ─┐                       Vultr instance "msj-host" (AlmaLinux 10, SELinux enforcing)
 Android app ┤  NetBird (WireGuard)  ┌───────────────────────────────────────────────────────┐
             └──────── wt0 ─────────►│ Caddy :443 on 100.x.y.z only (TLS, cert via DNS-01)   │
                                     │   └─► msj-serve 127.0.0.1:8765 (API + web UI)         │
 public internet ──► eth0: dropped   │ msj-worker ──► RunPod Serverless (HTTPS out)          │
 (Vultr firewall group + firewalld)  │ /srv/msj = LUKS volume (Block Storage): data, secrets │
                                     └───────────────────────────────────────────────────────┘
 DNS: journal.example.com  A  100.x.y.z   (public record, private address, "DNS only")
 Backups: restic, encrypted on the box ──► Backblaze B2
```

Four independent things keep the public internet out:

1. **Vultr firewall group:** no allow rule that matches real traffic.
2. **firewalld:** default zone `drop`, with only `wt0` trusted.
3. **Caddy:** binds only the NetBird address.
4. **The API:** listens only on loopback.

None of them needs an inbound port for the VPN, because NetBird dials out.

### The layout on the box

| Path | On | Owner, mode | Holds |
|---|---|---|---|
| `/srv/msj` | LUKS volume | root 0755 | mount point (a root-owned empty directory while locked) |
| `/srv/msj/data` | LUKS | msj 0700 | `MSJ_DATA_DIR`: `journal.db` (+ `-wal`, `-shm`), `audio/`, `token.secret` (0600) |
| `/srv/msj/secrets` | LUKS | root 0700 | `app.env`, `caddy.env`, `backup.env`, `restic-password` (all 0600) |
| `/srv/msj/caddy` | LUKS | caddy 0700 | TLS certificate, key and ACME account |
| `/srv/msj/backup` | LUKS | root:msj 0710 | nightly snapshot, pre-migration snapshots, replaced databases |
| `/srv/msj/restore` | LUKS | root 0711 | restore drill and manual restore scratch space |
| `/srv/msj/swapfile` | LUKS | root 0600 | 2 GiB swap (memory pages are plaintext, so not on the boot disk) |
| `/etc/msj/config.toml` | boot disk | root:msj 0640 | app configuration, no secrets |
| `/etc/msj/msj.env` | boot disk | root:msj 0640 | paths, `HF_HUB_OFFLINE=1` |
| `/etc/msj/host.env` | boot disk | root 0644 | `MSJ_HOSTNAME`, `MSJ_BIND_IP`, `MSJ_LUKS_DEVICE` |
| `/etc/msj/healthchecks.env` | boot disk | root 0600 | Healthchecks.io ping URLs (boot disk on purpose, see section 9) |
| `/opt/msj/releases/<id>` | boot disk | root 0755 | one virtualenv + web bundle per release; `/opt/msj/current` links to the live one |
| `/var/lib/msj/hf` | boot disk | msj 0700 | Hugging Face model cache (public weights) |
| `/usr/local/sbin/msj-*` | boot disk | root 0755 | the operations scripts in `deploy/scripts/` |

The boot disk holds nothing private. It can be rebuilt from this repository.
Everything that cannot be rebuilt is on the LUKS volume and in the restic
backups.

## 3. Before you start

Settle these before deployment day; most are account actions that are slow
or awkward to do mid-deploy. Every credential goes into your password manager
as you create it.

### Decisions

| Decision | Recommendation | Why |
|---|---|---|
| **Region** | The nearest region that sells **NVMe Block Storage**. As of 2026-11 that is ams, atl, blr, ewr, lax, lhr, mxp, nrt, ord, sea, sgp or syd. | The encrypted data volume is a Block Storage volume. NVMe is in 12 regions only (HDD is wider, minimum 40 GB). Volumes attach within one region only, and an instance cannot move region in place. |
| **Plan** | Cloud Compute, **High Performance AMD, 2 vCPU / 4 GB / 100 GB** (`vhp-2c-4gb-amd`, $24/month). | Measured on the release: each of the API and worker peaks about 0.9 GB with CPU torch and bge-small loaded. The two together plus Caddy, NetBird and the OS come to about 2.2 GB, and there is 2 GiB swap on top. 2 GB plans ($15-18) would live in swap. Dedicated CPU ($40) buys nothing for one user. |
| **Data volume** | NVMe Block Storage, **20 GB** (about $2/month). | Holds the database, audio, secrets, certificates and a 2 GiB swapfile. It resizes later. |
| **OS** | **AlmaLinux 10 x64**. | Same family as the earlier DigitalOcean box (SELinux, dnf, firewalld). Python 3.14 is in AppStream, so the interpreter gets security errata through `dnf` and no uv-managed Python is needed. |
| **Journal hostname** | `journal.<a domain you own>`. Preferably a **domain used for nothing else**. | The DNS token can rewrite every record in its zone. On a dedicated zone, a leaked token can only hurt this app. |
| **DNS provider** | **Cloudflare** (free DNS, per-zone API tokens). | `caddy-dns/cloudflare` is maintained, and its token can be scoped to DNS edits on one zone. Vultr DNS also works, but its credentials are account API keys or IAM service users, with no confirmed per-domain scope. To use another provider, see "DNS provider" in section 8. |

### Accounts and credentials

1. **[YOU] Vultr.**
   - **Turn on 2FA:** a hardware key (WebAuthn/FIDO2) or TOTP. Store the backup codes.
   - **Disable API access** (Account > API). Nothing in this deployment uses the Vultr API, and an unused API key is pure risk. If you later need it, restrict it by IP and use an IAM service user with the narrowest policy.
   - **Turn on maintenance emails:** Manage User > Preferences > "Outage and Maintenance" notifications. A maintenance reboot locks the journal until you unlock it.
2. **[YOU] NetBird** (`app.netbird.io`).
   - **Account:** sign up with email and password plus a passkey or TOTP, not a social login. Whoever holds this login is inside the network.
   - **Peer approval:** Settings > enable it.
   - **Your PC:** join it now, interactively, and check `netbird status` shows `Connected`:

     ```bash
     curl -fsSL https://pkgs.netbird.io/install.sh | sh
     sudo netbird up
     ```

   - **Default policy:** read it on Access Control once. "All peers to all peers" is what makes SSH to the box work in step 7.
   - **Setup key:** the server's setup key is created on the day (step 5), because it expires within a day.
3. **[YOU] Domain and Cloudflare.**
   - **Domain and zone:** register the domain (Cloudflare Registrar or Porkbun sell at cost) and add the zone to Cloudflare.
   - **API token:** My Profile > API Tokens > Create Token > template **Edit zone DNS** > Zone Resources: *Include, Specific zone, your zone*. Leave **TTL unset**, so no second silent expiry date exists. Leave the **IP filter empty for now**: step 13 offers to set it to the box's public IPv4 once that exists, and section 9's certificate check catches it going stale.
   - **Renewal:** note the domain's expiry date in your calendar a month ahead. A lapsed domain breaks access and certificate renewal on the same day.
4. **[YOU] Backblaze B2.**
   - **Account:** 2FA on.
   - **Bucket:** create a **private** bucket, e.g. `<something>-journal-backup`.
   - **Lifecycle:** set Lifecycle Settings to **"Keep prior versions for this number of days: 30"**. Not the default, which keeps every version forever, so pruning never frees space. Not "keep only the last version", which lets a mistyped or malicious `restic forget` destroy history immediately. Thirty days leaves a month in which the master account, whose credentials never touch the box, can roll the repository back.
   - **Key:** Application Keys > Add a New Application Key: access to **this bucket only**, **Read and Write** (prune deletes files, so read-only or write-only breaks retention).
   - **Note:** the `keyID`, the `applicationKey` (shown once) and the bucket's **S3 endpoint** (e.g. `s3.us-west-004.backblazeb2.com`).
5. **[YOU] Healthchecks.io** (free tier). Create five checks and note each ping URL:

   | Check | Period | Grace |
   |---|---|---|
   | `msj-health` | 5 minutes | 10 minutes |
   | `msj-backup` | 1 day | 2 hours |
   | `msj-cert` | 1 day | 1 day |
   | `msj-verify` | 7 days | 6 hours |
   | `msj-drill` | 31 days | 1 day |

   Add an email (and phone push, if you use the app) integration.
6. **[YOU] RunPod.** You already have the endpoints (`docs/phase0-runpod.md`). Create a dedicated API key for this box, restricted to Serverless if the console offers it, so it can be revoked on its own.
7. **[YOU] Password manager entries to create** (empty ones are fine until the day):
   - **Credentials:**
     - Vultr root password (the console login, step 6);
     - LUKS passphrase (step 9);
     - restic repository password (step 11);
     - B2 application key;
     - Cloudflare token;
     - RunPod key;
     - the journal's bearer token (step 13).
   - **Attachments:**
     - the LUKS header backup (step 9);
     - your filled-in `/etc/msj/host.env`.
8. **[PC] Workstation tools:**
   - NetBird connected;
   - an ed25519 SSH key (`ssh-keygen -t ed25519` if you have none);
   - `podman`;
   - `python3.14`;
   - Node 22+ with `pnpm` (see `web/README.md`).

## 4. Provisioning, step by step

Steps 1-6 happen in web consoles and on your PC. From step 7 on you work on
the box over NetBird. There is **never** a public SSH port: cloud-init enrols
the box in NetBird and closes the host firewall during first boot.

### Step 1. Vultr account security [YOU]

Do the Vultr items in "Before you start" if not already done: 2FA, API
disabled, maintenance emails.

**Verify:** sign out and back in. The second factor is requested.

### Step 2. Register your SSH key on Vultr [YOU]

Account > SSH Keys > Add SSH Key. Paste `~/.ssh/id_ed25519.pub`. Do this
**before** opening the deploy form, so the key is selectable there.

**Verify:** the key is listed under SSH Keys.

### Step 3. Create the firewall group [YOU]

Network > Firewall > Add Firewall Group, named `msj`.

**A firewall group with no rules filters nothing.** Vultr documents that the
default drop only takes effect once the group has at least one rule. So add
exactly one rule that can never match real traffic:

- IPv4: **accept, TCP, port 9, source `192.0.2.1/32`**. That source is
  TEST-NET-1 (RFC 5737), a documentation range that is never routed.

Add no IPv6 rule: the instance gets no public IPv6 (step 6). Never add a rule
for `100.x` addresses. Those exist only inside the WireGuard tunnel, and the
firewall group only ever sees encrypted UDP between public addresses.
Outbound traffic is not filtered by firewall groups at all.

**Verify:** the group lists one inbound IPv4 rule, and nothing else.

### Step 4. Build the release tarball [PC]

On an up-to-date `main`, with a clean working tree:

```bash
deploy/scripts/build-release.sh
```

This takes about a minute. It writes
`/workspace/build/msj-releases/msj-release-<id>.tar.gz` (or
`~/.cache/msj-releases/`). The tarball contains:

- the backend wheel;
- the built web bundle;
- a Caddy binary with the Cloudflare DNS module (built by `xcaddy` in podman);
- this `deploy/` directory.

The box never gets Node or the source tree.

**Verify:** the script ends by printing the tarball path and its sha256.

### Step 5. Create the server's NetBird setup key and the user-data [YOU, PC]

NetBird dashboard > Setup Keys > Create Setup Key:

- name `msj-host`;
- **Reusable: off**, **usage limit 1**;
- **expires in 1 day**;
- **Ephemeral: off**.

Copy the key.

Copy `deploy/cloud-init.yaml` to a scratch file outside the repository and
replace `REPLACE_WITH_ONE_OFF_SETUP_KEY` with the key. Never commit the
filled-in copy.

The key goes into user-data, which any process on the box can read from the
metadata service at `169.254.169.254`, and which stays visible in the Vultr
account. That is why the key is single-use and short-lived, and why step 7
revokes it. After first boot it is worthless.

Peers enrolled with a setup key do not expire their login. For the server
that is exactly right: the earlier DigitalOcean box had to turn login expiration off by
hand, because an expired server login is an outage with no way in.

**Verify:** the key shows usage 0 of 1 in the dashboard.

### Step 6. Deploy the instance and its volume [YOU]

Deploy > Deploy New Server:

| Field | Value |
|---|---|
| Type | Cloud Compute, High Performance, AMD |
| Location | your region (one that sells NVMe Block Storage) |
| Image | AlmaLinux 10 x64 |
| Plan | 2 vCPU / 4 GB / 100 GB (`vhp-2c-4gb-amd`) |
| Additional features | **Auto Backups: off.** **IPv6: off.** **VPC: off.** **Cloud-Init User-Data: on**, paste the filled-in file from step 5. |
| SSH Keys | your key from step 2 |
| Firewall group | `msj` |
| Server hostname and label | `msj-host` |

Why those choices:

- **Auto Backups off.** They image the boot disk only, not Block Storage. The
  boot disk is deliberately empty of anything private and is rebuilt from the
  repository, so they would cost 20% of the plan to back up the one disk that
  needs no backup. Restic is the backup story (section 7).
- **IPv6 off.** Nothing listens on it, and a second address family is a
  second set of firewall rules to get right in exchange for nothing.
- **VPC off.** A single instance has no one to talk to privately.
- **`msj-host`.** The hostname names the NetBird peer
  (`msj-host.netbird.cloud`) and the restic snapshot group. Keep it on any
  replacement box. It encodes neither plan nor region, so it never becomes a
  lie.

When the instance is running, open its **Overview** page and note:

- **the root password.** Change it on the box in step 8 and store the new one;
  it is only ever needed for the web console.
- **the public IPv4.** Only for the checks in steps 7 and 13.

Then create the data volume: Cloud Storage > Block Storage > Add:

- **NVMe**, **20 GB**;
- the **same region**;
- label `msj-data`;
- **attach it to `msj-host`**.

Attaching can restart the instance (unconfirmed in Vultr's docs for the web
console; the API documents a "live" attach). That is why it happens now,
before anything is configured.

**Verify:** after a minute the new peer `msj-host` appears on the NetBird
dashboard's Peers page. With peer approval on, it may show as pending.

If no peer appears within five minutes:

1. Open the instance's **View Console** and log in as root with the Overview
   password.
2. Read `/root/netbird-up.log`.
3. The usual causes are an expired or used setup key, or a typo in the
   pasted key. Create a new key and run
   `netbird up --setup-key <key>` there.

### Step 7. Approve the peer, reach the box over NetBird, close the loop [YOU, PC]

1. **[YOU]** On the Peers page, approve `msj-host` if it is pending. Open the
   peer and confirm:
   - the name is `msj-host`;
   - login expiration is not applied (setup-key peers are exempt).

   Note its **NetBird IP**, `100.x.y.z`.
2. **[YOU]** Setup Keys: **revoke** the `msj-host` key, even though it is used
   up.
3. **[PC]** Connect by the literal address first, since name resolution is a
   separate mechanism from the tunnel:

   ```bash
   ssh root@100.x.y.z
   ```

   Then by name, which is what everything else uses:

   ```bash
   ssh root@msj-host.netbird.cloud
   ```

4. **[BOX]** Check the first-boot work:

   ```bash
   cat /root/netbird-up.log         # ends with "netbird up exit status 0"
   ls /root/netbird-setup-key 2>&1  # "No such file": cloud-init shredded it
   firewall-cmd --get-default-zone  # drop
   firewall-cmd --get-active-zones  # the public interface under drop, wt0 under trusted
   sshd -T | grep -E '^(passwordauthentication|permitrootlogin)'  # no / prohibit-password
   ```

5. **[PC]** Prove the public address is closed. Run this from your PC: the
   packets go over the internet, not the tunnel.

   ```bash
   nc -vz -w 5 203.0.113.10 22    # must time out, not "refused" or "succeeded"
   nc -vz -w 5 203.0.113.10 443   # must time out
   ```

**Verify:** both SSH commands work, and both `nc` probes time out. A
"Connection refused" means a packet reached the box, so a firewall layer is
missing; fix that before going on.

### Step 8. Install the host [BOX, PC]

**[PC]** Copy the release:

```bash
scp /workspace/build/msj-releases/msj-release-<id>.tar.gz root@msj-host.netbird.cloud:/root/
```

**[BOX]**

```bash
mkdir -p /root/rel/<id> && tar -xzf /root/msj-release-<id>.tar.gz -C /root/rel/<id>
/root/rel/<id>/deploy/scripts/bootstrap.sh host
passwd    # set the root password to a new diceware passphrase; store it
dnf -y upgrade
dnf needs-restarting -r || reboot     # reboot now if a kernel update arrived
```

`bootstrap.sh host` is idempotent and safe to rerun. It:

- installs the packages: EPEL, `python3.14` from AppStream, `restic`,
  `sqlite`, `cryptsetup`;
- re-asserts the firewalld setup;
- writes the sshd drop-in;
- turns off core dumps (`systemd-coredump` `Storage=none`,
  `fs.suid_dumpable=0`, abrt if present);
- caps journald at 500 MB;
- sets `dnf-automatic` to security updates only, never rebooting;
- creates the `msj` and `caddy` system accounts and the directories;
- installs the scripts, units, Caddyfile, `msj.env`, the production config
  and the Caddy binary;
- labels the swapfile path for SELinux;
- enables the five timers.

It never enables the journal itself.

The root password is only for the Vultr web console (sshd refuses passwords).
Make it lowercase diceware words joined by hyphens: the console expects a US
keyboard layout, and you may be typing it there under pressure.

**Verify:**

```bash
getenforce                                       # Enforcing
systemctl list-timers 'msj-*' --no-pager         # five timers
systemctl is-enabled caddy msj-serve msj-worker  # disabled / static, never enabled
/usr/local/bin/caddy list-modules | grep dns.providers   # dns.providers.cloudflare
python3.14 --version                             # Python 3.14.x
```

If the reboot happened, reconnect over NetBird. That reconnect also proves
NetBird starts on boot.

### Step 9. Encrypt the data volume [YOU, BOX]

Choose the **LUKS passphrase**: six or more random words (your password
manager's passphrase generator), lowercase, joined with hyphens.

- **Typing it:** it gets typed by hand a few times a year, possibly into the
  Vultr web console, which assumes a US layout.
- **Paper:** write it on paper too, two copies, in two places. Paper cannot be
  exfiltrated and survives the password manager being unavailable.

**[BOX]** Find the volume. It is the 20 GB disk with no partitions,
normally `/dev/vdb`:

```bash
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS
wipefs -n /dev/vdb      # must print nothing: an empty disk
```

Format, open and make the filesystem:

```bash
cryptsetup luksFormat --type luks2 /dev/vdb          # type YES, then the passphrase twice
uuid=$(cryptsetup luksUUID /dev/vdb); echo "$uuid"
cryptsetup open "/dev/disk/by-uuid/$uuid" msj-data
mkfs.ext4 -L msj-data /dev/mapper/msj-data
```

The volume is always addressed by its LUKS UUID, never `/dev/vdb`. The UUID
lives in the LUKS header, so it survives detaching, reattaching and device
renumbering.

Back up the LUKS header before any data goes on the volume:

```bash
cryptsetup luksHeaderBackup /dev/vdb --header-backup-file /root/luks-header-msj-data.img
```

**[PC]** Fetch it, store it as a password-manager attachment, then delete it
from the box:

```bash
scp root@msj-host.netbird.cloud:/root/luks-header-msj-data.img .
```

**[BOX]**

```bash
shred -u /root/luks-header-msj-data.img
```

A damaged header makes the volume unreadable **with the right passphrase in
hand**. The backup is the only fix. It also contains the encrypted master key,
so it must never sit next to the volume (on the boot disk) longer than this
step. Take it again whenever a key slot is added or removed.

Write the host settings (the NetBird address from step 7, your hostname, the
UUID):

```bash
vi /etc/msj/host.env        # MSJ_HOSTNAME, MSJ_BIND_IP, MSJ_LUKS_DEVICE=/dev/disk/by-uuid/<uuid>
ip -4 -o addr show wt0      # MSJ_BIND_IP must equal this address
```

Mount it through the same script every later unlock uses, then lay out the
volume:

```bash
msj-unlock --no-start       # already open, so it only mounts
/root/rel/<id>/deploy/scripts/bootstrap.sh volume
```

`bootstrap.sh volume` creates the directories with the owners in section 2.
It then makes and enables the 2 GiB swapfile (written with `dd`, labelled
`swapfile_t`). It finishes by listing the secrets that are still missing.

**Verify:**

```bash
findmnt /srv/msj                      # SOURCE /dev/mapper/msj-data, options nodev,nosuid,noexec
swapon --show                         # /srv/msj/swapfile
ls -la /srv/msj                       # data msj 700, secrets root 700, caddy caddy 700
```

Then prove the paper copy. Lock, unlock with the passphrase read from paper,
and verify again:

```bash
msj-lock && msj-unlock --no-start
```

There is no `/etc/crypttab` or `/etc/fstab` entry, deliberately. The volume
is unlocked by a human, and a boot that waited for a passphrase would hang
before NetBird came up.

### Step 10. Point the hostname at the box [YOU, PC]

**[YOU]** Cloudflare > your zone > DNS > Add record:

- **Type A**, **Name** `journal` (or your chosen label), **IPv4** `100.x.y.z`;
- **Proxy status: DNS only** (grey cloud);
- **TTL 5 min**.

A private address in public DNS is deliberate and harmless: it is
unroutable except over the VPN.

- **Proxied would break it.** Cloudflare's edge cannot reach a `100.x`
  address.
- **The short TTL** matters because the address changes if the peer is ever
  re-enrolled. The TTL is how long that repair takes to reach your phone.

**[PC]**

```bash
dig +short journal.example.com     # exactly 100.x.y.z
```

**Verify:** that `dig` result, from the PC and not from the box. A wrong A
record does not stop the certificate (DNS-01 never reads it). Caddy would
happily get a valid certificate for a name that points nowhere.

### Step 11. Write the secrets [BOX]

All secret files are root-owned, mode 600, on the encrypted volume. systemd
reads them as root and hands the values to the services. `read -rs` keeps
each value off the screen and out of shell history; paste it from the
password manager.

```bash
umask 077
cd /srv/msj/secrets

read -rs -p 'RunPod API key: ' v && printf 'RUNPOD_API_KEY=%s\n' "$v" > app.env; echo
read -rs -p 'Cloudflare API token: ' v && printf 'CF_API_TOKEN=%s\n' "$v" > caddy.env; echo
read -rs -p 'restic repository password (from the password manager): ' v && printf '%s\n' "$v" > restic-password; echo
unset v

cp /root/rel/<id>/deploy/env/backup.env.example backup.env && vi backup.env   # repository URL, B2 key id and key
vi /etc/msj/healthchecks.env                                                    # the five ping URLs
ls -la /srv/msj/secrets /etc/msj
```

The restic password: generate a long random one in the password manager
first, then paste it here. Without it the backups are ciphertext nobody can
read, including you.

Then initialise the repository, once:

```bash
set -a; . /srv/msj/secrets/backup.env; set +a; export RESTIC_CACHE_DIR=/srv/msj/restic-cache
restic init
```

**Verify:** `restic init` prints "created restic repository". `ls -la` shows
every file `-rw-------` and owned by root.

### Step 12. Install the journal [BOX]

```bash
msj-release install /root/msj-release-<id>.tar.gz
```

What it does:

1. Builds `/opt/msj/releases/<id>/venv` from `/usr/bin/python3.14`.
2. Installs every dependency from the release's hashed lock
   (`requirements/deploy.txt`, exported from `backend/uv.lock`) with
   `--require-hashes`. torch is the `+cpu` build from the PyTorch CPU index,
   so no CUDA wheels are ever pulled.
3. Installs the journal's wheel without dependencies, then runs `pip check`.
4. Records the environment (`requirements.freeze`) and checks that no
   `nvidia-*` package slipped in.
5. Downloads bge-small-en-v1.5 at its pinned revision into `/var/lib/msj/hf`.
   This is the only time the box talks to the Hugging Face Hub; the services
   run with `HF_HUB_OFFLINE=1`.
6. Switches `/opt/msj/current`.
7. Runs `msj migrate` (the `msj-migrate.service` oneshot).
8. Starts `msj.target`.
9. Waits for the API and a fresh worker heartbeat.

The first install takes a few minutes (torch is about 200 MB).

Create the bearer token. It prints once; put it in the password manager now:

```bash
msj-cli create-token
```

**Verify:**

```bash
systemctl is-active caddy msj-serve msj-worker      # active active active
journalctl -u caddy --no-pager | grep -iE 'certificate obtained|error'
curl -s http://127.0.0.1:8765/api/v1/health          # {"status":"ok"}
```

**[PC]**

```bash
curl -s https://journal.example.com/api/v1/health
```

This is the one check that exercises DNS, the VPN, Caddy, the certificate and
the app together.

If the certificate is missing, read `journalctl -u caddy`. The usual causes:

- a token scoped to the wrong zone;
- a proxied record;
- an `MSJ_HOSTNAME` typo.

### Step 13. Prove every scheduled job once, by hand [BOX, YOU]

Left to the timers, the first backup runs at 03:30 UTC and announces any
failure only as a missing ping. Run each one now:

```bash
systemctl start msj-backup.service        && journalctl -u msj-backup -n 5 --no-pager
systemctl start msj-backup-verify.service && journalctl -u msj-backup-verify -n 5 --no-pager
systemctl start msj-restore-drill.service && journalctl -u msj-restore-drill -n 5 --no-pager
systemctl start msj-cert-check.service    && journalctl -u msj-cert-check -n 3 --no-pager
msj-health
```

**Verify:**

- each unit finishes without "Failed";
- the drill prints `restore OK: entries=... audio=... migrations=...`;
- `msj-health` prints `all checks passed`;
- all five checks on Healthchecks.io show green.

Optional, now that the public IPv4 is known: add it as the Cloudflare
token's Client IP Address Filter. That way a leaked token is useless
elsewhere. It must be re-pointed on any rebuild; the certificate check
catches it if you forget.

### Step 14. Connect the clients [YOU]

- **PC browser.** Open `https://journal.example.com` and sign in with the
  token. The session cookie is `__Host-msj_session` (Secure, `Path=/`), so
  sign-in only works over that HTTPS name.
- **Android.**
  1. Install the NetBird app (`io.netbird.client`) and sign in as yourself.
  2. Approve the phone on the Peers page.
  3. In the journal app, set the server to `https://journal.example.com` and
     paste the bearer token.

  The phone resolves the name through public DNS to `100.x.y.z`, which
  NetBird routes. The same certificate serves both clients.

**Verify:**

- the web UI lists entries (none yet);
- the Android app's health or sign-in succeeds over mobile data with Wi-Fi off;
- an entry made on one client shows on the other.

### Step 15. Rehearse a reboot [YOU, BOX]

A reboot is the event this design turns into a manual procedure. Rehearse it
now, while nothing depends on it:

```bash
msj-lock && reboot
```

Reconnect over NetBird (about a minute), then:

```bash
msj-unlock
```

**Verify:** `msj-unlock` ends with `journal is up`, and the web UI works.

Also open the Vultr **View Console** once and log in as root with the new
password. That is the fallback path; you want to have used it before you
need it.

## 5. Unlock after a reboot

After any reboot (yours, a kernel update you chose to take, or Vultr host
maintenance) the volume is locked and the journal is down by design. The
health check pings `/fail` with "encrypted volume is locked" every five
minutes until you act.

1. **[PC]** `ssh root@msj-host.netbird.cloud` (or `ssh root@100.x.y.z` if the
   name does not resolve; your own NetBird agent answers that name).
2. **[BOX]** `msj-unlock`, then type the LUKS passphrase. It:
   - opens and mounts the volume (`nodev,nosuid,noexec`);
   - turns swap on;
   - checks the data directory's ownership;
   - starts `msj.target` and waits for the API;
   - starts a catch-up backup;
   - runs `msj-health`.
3. **Verify:** it ends with `journal is up`. Healthchecks.io goes green.
4. If the box was down across a Sunday or the 1st of the month, also run
   `systemctl start msj-backup-verify.service` and
   `systemctl start msj-restore-drill.service`. Their timers fired while the
   volume was locked; each refused and reported, and the next scheduled run
   is a week or a month away.

**If NetBird is down** (no SSH over the VPN), use the Vultr web console:

1. Instance > View Console. This needs your Vultr login and second factor.
2. Log in as `root` with the console password.
3. Run `msj-unlock`.

To enter the passphrase, type it, or use the console's control bar >
Clipboard: paste the text, then **Paste**, which sends it as keystrokes. The
console assumes a US layout, which is why the passphrase is plain lowercase
words and hyphens. Then fix NetBird: `systemctl status netbird`,
`netbird status -d`, `journalctl -u netbird`.

Never put the passphrase in a file on the box, in user-data, or in a script.
LUKS protects a detached volume, a leaked volume snapshot, and a
decommissioned disk. It does not protect a running, unlocked machine from its
host, and Vultr's console reaches the VM out-of-band regardless of any
firewall. Be honest with yourself about that boundary.

## 6. Deploying a new version

**[PC]** Merge to `main`, then build:

```bash
git switch main && git pull
deploy/scripts/build-release.sh
scp /workspace/build/msj-releases/msj-release-<id>.tar.gz root@msj-host.netbird.cloud:/root/
```

**[BOX]**

```bash
mkdir -p /root/rel/<id> && tar -xzf /root/msj-release-<id>.tar.gz -C /root/rel/<id>
/root/rel/<id>/deploy/scripts/bootstrap.sh host     # scripts, units, Caddy from this release
msj-release install /root/msj-release-<id>.tar.gz
```

Rerunning `bootstrap.sh host` every time keeps the box's scripts and units
identical to the release. If the Caddy binary changed, it restarts Caddy. It
never overwrites `/etc/msj/config.toml`. If the release's
`deploy/config.production.toml` differs from the live config, it prints the
`diff` command to review, so you can apply the change by hand.

`msj-release install`:

- **Deploys safely mid-batch:** stopping the worker cancels the in-flight
  RunPod job and requeues it without using an attempt; the new worker runs it.
- **Installs exactly the locked dependencies.** To change them, edit
  `backend/pyproject.toml`, run `scripts/lock.sh` on the PC, commit, and
  build a new release; `build-release.sh` refuses a stale lock.
- **Snapshots** the database to `/srv/msj/backup/pre-migrate/` before
  migrating. It keeps the last five snapshots and the last four releases.

**Verify:** `release <id> is live`, then
`curl -s https://journal.example.com/api/v1/health` from the PC, and
`msj-release list` shows `*` on the new id.

**Rolling back:**

```bash
msj-release rollback               # previous release, same database
msj-release rollback --restore-db  # also restores the pre-migration snapshot
```

`rollback` compares the migration counts. If the new release applied
migrations, it refuses without `--restore-db` and tells you so: old code
against a newer schema is not something to discover in production. With
`--restore-db`:

- **What is lost:** every write since the deploy.
- **What is kept:** the replaced database stays under
  `/srv/msj/backup/replaced-<stamp>/`, so nothing is destroyed.

**Host updates.**

- `dnf-automatic` applies security updates daily and never reboots.
- When `python3.14` itself is updated, the running services keep the old
  interpreter until restarted: `dnf needs-restarting -s` lists them, and
  `systemctl restart msj-serve msj-worker` applies it.
- For a kernel update, check `dnf needs-restarting -r` and take the reboot
  deliberately: `msj-lock && reboot`, then section 5.

## 7. Backups and restore

### Design

| Job | When (UTC) | What | Reports to |
|---|---|---|---|
| `msj-backup` | daily 03:30 | consistent SQLite snapshot, then restic backup to B2 | `HC_BACKUP_URL` |
| `msj-backup-verify` | Sundays 05:00 | `forget --prune` (14 daily, 8 weekly, 12 monthly), then `check --read-data-subset` of a rotating quarter (all data read every 4 weeks) | `HC_VERIFY_URL` |
| `msj-restore-drill` | 1st of month 06:00 | restore the latest snapshot to scratch space and prove it usable | `HC_DRILL_URL` |

**What goes into each snapshot:**

- `journal.db`, as a snapshot taken by SQLite's online backup API (see
  below);
- `audio/`;
- `token.secret`, the bearer-token HMAC key: without it the restored token
  hash is useless and every client needs a new token;
- `/srv/msj/secrets`;
- `/etc/msj`.

**What is left out, deliberately:**

- the live `journal.db`, `-wal` and `-shm`;
- Caddy's state (it reissues the certificate from the token);
- swap, caches and scratch space;
- the releases and the model (rebuilt from source).

**Consistency.** In WAL mode the newest commits live in `journal.db-wal`. A
copy of `journal.db` alone is missing them; a copy of both taken while the
app writes can be torn. So the backup never copies the live file:

- `msj-backup` runs `sqlite3 journal.db ".backup <snapshot>"` as the `msj`
  account. That is SQLite's online backup API, and it produces a consistent
  point-in-time copy while the app keeps running. Running it as `msj` means no
  root-owned `-shm` or `-wal` is ever created next to the live database,
  which the app could not open afterwards.
- `PRAGMA integrity_check` then runs on the snapshot. A failure stops the job
  and alerts, and older snapshots stay in the repository.
- `VACUUM INTO` would also be consistent. `.backup` is used because it keeps
  the page layout stable from night to night, so restic deduplicates it well.

Audio files are content-addressed and written once, then the snapshot is
taken, then restic reads the audio directory. The only possible mismatch is an
entry deleted in those seconds; its row would come back without its file. The
drill would report that, and it is harmless.

**Encryption and keys.** restic encrypts on the box, so B2 holds ciphertext
only.

- **The restic password:** its only copies are the file on the LUKS volume
  and your password manager.
- **The B2 application key:** it is on the box, so B2's 30-day version
  retention is what protects the history from a compromised box.
- **The secrets:** they ride inside the snapshot they unlock, which is
  harmless (anyone who can read it already has them). It makes a total-loss
  restore depend on a mechanism rather than on memory.

**Recovery point:** up to 24 hours. restic deduplicates, so moving
`msj-backup.timer` to every 6 hours costs almost nothing if that ever
matters.

### Restore A: undo a bad deploy

Use `msj-release rollback --restore-db` (section 6).

### Restore B: replace the live database from a backup [BOX]

Use this for corruption, a destructive mistake, or a failed drill on the
live data. **Tested** in an AlmaLinux 10 container against a real release:
an entry written after the backup disappears, ownership and modes come back
right, and the existing bearer token still authenticates.

```bash
msj-unlock --no-start                                  # if the volume is locked
systemctl stop msj-worker.service msj-serve.service
set -a; . /srv/msj/secrets/backup.env; set +a; export RESTIC_CACHE_DIR=/srv/msj/restic-cache
restic snapshots --tag msj                             # pick one; "latest" below
d=$(mktemp -d -p /srv/msj/restore restore.XXXXXX)
restic restore latest --tag msj --target "$d"

# Move the live database aside, WAL and shared-memory files too: a stale
# -wal next to a replaced journal.db would be replayed into it.
aside=/srv/msj/backup/replaced-$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 700 -o msj -g msj "$aside"
for f in journal.db journal.db-wal journal.db-shm; do [ -e /srv/msj/data/$f ] && mv /srv/msj/data/$f "$aside/"; done
install -m 600 -o msj -g msj "$d/srv/msj/backup/nightly/journal.db" /srv/msj/data/journal.db

# Audio: add back what is missing, never overwrite or delete newer files.
cp -an "$d/srv/msj/data/audio/." /srv/msj/data/audio/
# The token key, only if it was lost (replacing it invalidates the current token):
[ -f /srv/msj/data/token.secret ] || install -m 600 -o msj -g msj "$d/srv/msj/data/token.secret" /srv/msj/data/token.secret

systemctl start msj-migrate.service      # brings an older snapshot up to the deployed schema
systemctl start msj.target
rm -rf -- "$d"
msj-health
```

**Verify:** `msj-health` prints `all checks passed`, and the web UI shows the
entries you expect. Delete `$aside` once you are sure.

### Restore C: total loss (instance or volume gone) [YOU, BOX]

Everything needed is in the password manager and the B2 repository.

1. Provision a new instance and volume with section 4, steps 2-9:
   - **Hostname:** `msj-host` again. restic groups snapshots by host; a new
     name starts a new group, and the old one is kept forever by retention.
   - **Firewall group:** the existing `msj` group still applies.
   - **LUKS passphrase:** may be new.
2. NetBird gives the new peer a new `100.x` address. Update the A record
   (step 10) and `MSJ_BIND_IP`.
3. Write only `backup.env` and `restic-password` by hand (step 11). They open
   the backup; everything else comes out of it:

   ```bash
   set -a; . /srv/msj/secrets/backup.env; set +a; export RESTIC_CACHE_DIR=/srv/msj/restic-cache
   d=$(mktemp -d -p /srv/msj/restore restore.XXXXXX)
   restic restore latest --tag msj --target "$d"
   cp -an "$d/srv/msj/secrets/." /srv/msj/secrets/
   cp -a "$d/etc/msj/config.toml" /etc/msj/config.toml       # your live config, not the template
   diff "$d/etc/msj/host.env" /etc/msj/host.env               # keep the NEW bind IP and LUKS UUID
   install -m 600 -o msj -g msj "$d/srv/msj/backup/nightly/journal.db" /srv/msj/data/journal.db
   install -m 600 -o msj -g msj "$d/srv/msj/data/token.secret" /srv/msj/data/token.secret
   cp -a "$d/srv/msj/data/audio" /srv/msj/data/
   rm -rf -- "$d"
   ```

4. Continue with step 12, but do **not** run `msj-cli create-token`. The
   restored database and `token.secret` keep the existing token valid, so the
   PC and the phone need no change. Then do steps 13 and 15.
5. Check that the restic snapshots continue under `msj-host`:
   `restic snapshots --tag msj`.

**What a total loss costs:**

- everything written since the last nightly backup;
- any credential rotated without updating the password manager.

**Locks.** If a job was killed mid-run, later runs fail with "repository is
already locked". Confirm nothing is running
(`systemctl status 'msj-backup*' msj-restore-drill`), then run
`restic unlock`. This is manual on purpose: clearing a lock a live job holds
can corrupt the repository.

## 8. Secrets and configuration

| File | Holds | Read by |
|---|---|---|
| `/srv/msj/secrets/app.env` | `RUNPOD_API_KEY` | msj-serve, msj-worker, msj-migrate, msj-cli (via systemd / msj-cli) |
| `/srv/msj/secrets/caddy.env` | `CF_API_TOKEN` | caddy.service |
| `/srv/msj/secrets/backup.env` | `RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | msj-backup, -verify, -restore-drill |
| `/srv/msj/secrets/restic-password` | restic repository password | restic |
| `/srv/msj/data/token.secret` | 32-byte HMAC key for the bearer token (the app refuses it unless mode 0600) | the app |
| `/etc/msj/healthchecks.env` | five ping URLs | the msj-* jobs |
| `/etc/msj/config.toml` | routes, limits, embedding model; no secrets | the app |

Templates for each are in `deploy/env/`.

**App settings that matter behind Caddy:**

- **`forwarded_allow_ips = "127.0.0.1,::1"`.** Caddy connects from loopback,
  and uvicorn trusts `X-Forwarded-For` and `-Proto` only from there. Client
  addresses, which the sign-in rate limit keys on, are therefore the real
  NetBird peers. Set it wider and any local process can spoof them. Set it
  empty and every client shares one rate-limit bucket.
- **`insecure_cookies = false`** (and never `MSJ_INSECURE_COOKIES`). The
  session cookie is then `__Host-msj_session`: Secure, `Path=/`, no `Domain`.
  It works only over HTTPS on the exact hostname.
- **`HF_HUB_OFFLINE=1`** in `/etc/msj/msj.env`. The model loads from
  `/var/lib/msj/hf` without contacting the Hub, which the API's network
  sandbox would block anyway.
  - **Changing the model:** to change `[embedding] model` or `revision`, edit
    the config and run `msj-release install` for the live tarball with
    `--force`. It loads offline first and downloads only when that fails.
- **`upload_limit_bytes`** must move together with `request_body max_size`
  in the Caddyfile (`max_size` = upload limit + 16 KiB).

**Token re-issue.** `msj-cli create-token` replaces the token: it ends every
browser session and invalidates the Android app's copy.

- **When to use it:** if the token leaks; after
  `TOKEN_REISSUE_REQUIRED` (logged when a database from before migration 009
  is opened, or when `token.secret` is missing); or after deliberately
  replacing `token.secret`.
- **Afterwards:** store the new token and update both clients.
- **Never run the app as root:** run `msj` through `msj-cli`. A root-run
  `msj` would create root-owned files in the data directory that the services
  then cannot open.

**Rotating the other secrets.** Edit the file, then restart what reads it:

| Secret | After editing, run |
|---|---|
| `app.env` | `systemctl restart msj-serve msj-worker` |
| `caddy.env` | `systemctl restart caddy` |
| `backup.env`, `restic-password` | nothing; the next run reads it |

For the restic password, use `restic key add` and then `restic key remove`.
Never just overwrite the file.

**Adding a LUKS passphrase:** `cryptsetup luksAddKey`. Then take a new header
backup (step 9).

**DNS provider.** The Caddy binary is built with
`github.com/caddy-dns/cloudflare`. For another provider:

1. Change the `--with` module in `deploy/scripts/build-release.sh`.
2. Change the `dns` line in `deploy/caddy/Caddyfile`. Syntax examples:
   - Porkbun: `dns porkbun { api_key ... api_secret_key ... }`
   - deSEC: `dns desec { token ... }`
   - Vultr: `dns vultr {env.VULTR_API_TOKEN}`
3. Change the variable name in `caddy.env`.
4. Run `caddy validate` (section 9).

Route53 can be scoped to the `_acme-challenge` record alone, the tightest
scope available.

## 9. Observability

**Dead-man switches** (Healthchecks.io). Every job pings `/start` and then
`/0` or `/<status>`, with a one-line reason as the body. The alert says what
failed and where the full log is:

| Check | Fails when |
|---|---|
| `msj-health` (5 min) | any of the following: the volume is locked; swap is off; `caddy`, `msj-serve` or `msj-worker` is inactive; the API fails on loopback; the API fails through Caddy over TLS on the NetBird address; the worker heartbeat is older than 60 s; the A record no longer matches `wt0`; the NetBird address differs from `MSJ_BIND_IP`; `/` or `/srv/msj` is at least 85% full. It lists every failing check in one message. |
| `msj-backup` (daily) | the snapshot fails `integrity_check`, restic fails, or the volume is locked |
| `msj-verify` (weekly) | prune or `check` reports a problem |
| `msj-drill` (monthly) | a restored database fails integrity; it lacks migrations or the token row; `token.secret` or an audio file is missing; or `msj migrate` cannot open the copy. A drill that stops running looks exactly like one that keeps passing, so alert on it. |
| `msj-cert` (daily) | the live certificate does not verify for `MSJ_HOSTNAME`, or has under 21 days left |

**Why the certificate check exists.** Caddy renews at 30 days remaining. A
renewal failure is silent: the old certificate stays valid while Caddy
retries into a log nobody reads. Then on one day the browser and the phone
both refuse the server. Causes include a revoked or expired DNS token, an IP
filter that no longer matches, a lapsed domain, or Caddy not running.

The check asks what a client asks, over a real handshake by name: does the
certificate verify, and for how long. Under 21 days means renewal has been
failing for over a week, with three weeks left to fix it.

The ping URLs live on the boot disk, so a job refused because the volume is
locked can still say so. The cost: anyone who reads the file can send false
"OK" pings. That needs root on the box, which is game over anyway.

**Run any check by hand:** `msj-health` gives an instant status report, and
`systemctl start msj-cert-check.service` runs the certificate check.

**Logs** (journald, capped at 500 MB, on the boot disk):

| What | Command |
|---|---|
| API, worker | `journalctl -u msj-serve -u msj-worker` (the API runs with no access log; app logs avoid entry content) |
| Caddy, certificates | `journalctl -u caddy` (no access log on purpose: request lines carry entry ids and search terms) |
| Jobs | `journalctl -u msj-backup -u msj-backup-verify -u msj-restore-drill -u msj-cert-check -u msj-health` |
| Deploys | `journalctl -u msj-migrate` |
| Security updates | `journalctl -u dnf-automatic` |
| NetBird | `netbird status -d`, `journalctl -u netbird`, `/root/netbird-up.log` (first boot) |
| Processing failures | the web UI's queue view, or `GET /api/v1/queue` with the bearer token |

**SELinux denials do not look like denials.** They surface as a plain
"permission denied". Check `ausearch -m AVC -ts recent` and `getenforce`. Fix
labels with `restorecon -Rv <path>`. Never `setenforce 0`.

**Sandbox review:** `systemd-analyze security msj-serve` (and the other
units). Validated scores on AlmaLinux 10 (systemd 257):

- `msj-serve`: 1.2
- `msj-worker`: 1.4
- `caddy`: 3.2

The API has no network beyond loopback (`IPAddressDeny=any`).

**Caddy config check** after editing the Caddyfile:

```bash
set -a; . /etc/msj/host.env; . /srv/msj/secrets/caddy.env; set +a
/usr/local/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

**Vultr panel.** It shows bandwidth and CPU graphs for the instance. There is
no Vultr agent for disk alerts; `msj-health` covers the disk.

## 10. What will bite

- **A Vultr firewall group with zero rules filters nothing.** It needs the
  inert rule from step 3. Prove it with the `nc` probes in step 7.
- **NVMe Block Storage is sold in only some regions.** Pick the region
  first, then check both the plan and NVMe Block Storage are offered.
- **Every reboot takes the journal down until you unlock it.** That includes
  Vultr maintenance you did not schedule. Keep maintenance emails on. The
  health check alerts every 5 minutes until you act.
- **Timers that fire while locked report failure.** That is correct, not
  noise. `msj-unlock` starts a catch-up backup; start the verify or the drill
  yourself if their day was missed.
- **The NetBird address is in public DNS.** Re-enrolling the peer issues a new
  `100.x` address. Update the A record and `MSJ_BIND_IP`, then
  `systemctl restart caddy`. `msj-health` reports either mismatch.
- **Home routers and some DNS services drop private-range answers** (DNS
  rebinding protection: OpenWrt/dnsmasq `rebind_protection`, some Android
  "Private DNS" providers). The symptom is that the name fails to resolve on
  one network only. Check with `dig journal.example.com` there; allow-list
  the domain in that resolver.
- **A Cloudflare record set to "Proxied" breaks everything** while looking
  configured. It must be DNS only.
- **A missing `MSJ_HOSTNAME` gives a cryptic Caddy error.** If
  `/etc/msj/host.env` lacks it, Caddy fails with "server block without any key
  is global configuration, and if used, it must be first". That message names
  neither the file nor the variable.
- **Never copy `journal.db` on its own, and never put a database back next to
  a stale `journal.db-wal`.** SQLite would replay the old WAL into it. Every
  procedure here moves all three files together.
- **Never run `/opt/msj/current/venv/bin/msj` as root.** Use `msj-cli`.
  Root-owned files in `/srv/msj/data` make the services fail with permission
  errors.
- **`token.secret` must be mode 0600.** Otherwise the app refuses it ("has
  mode ...; set its permissions to 0600"). A restore through these procedures
  keeps the mode.
- **`msj-cli create-token` logs every client out**, including the Android app.
- **`__Host-` cookies require HTTPS on the real hostname.** Sign-in by IP
  address or plain HTTP fails, by design.
- **A worker stop blocked on the network** (a RunPod request already in
  flight, up to 30 s) can outlast `TimeoutStopSec=30`; systemd then kills it
  and the job's lease expires before it is retried (up to `lease_s`).
- **A Dependabot dependency PR fails the `lock` gate** until `scripts/lock.sh`
  is run on its branch to refresh the hashed exports.
- **The AlmaLinux 10 image needs an x86-64-v3 CPU.** AMD EPYC High
  Performance hosts qualify. If an image ever fails to boot on another plan,
  that is the first suspect. AlmaLinux also publishes a v2 build.
- **Hostname.** Keep `msj-host`; the restic group and the NetBird peer name
  depend on it.
- **Billing.** Vultr bills stopped instances, and only destroying one stops
  charges. Block Storage bills separately, and survives instance deletion
  unless you delete it too.
- **The setup key stays visible in user-data.** It is in the Vultr account and
  the metadata service for the life of the instance. It is single-use and
  revoked, so it opens nothing.
- **The domain's expiry date is a deployment date.** Calendar it.
- **B2 lifecycle default is "keep all versions"**, so pruned data is never
  freed. Set the 30-day rule (section 3).
- **SELinux and the swapfile.** `swapon` fails with a bare "permission
  denied" unless the file is labelled `swapfile_t`; `bootstrap.sh volume`
  does this.
- **Outbound SMTP (port 25) is blocked on Vultr.** Nothing here sends mail;
  alerts come from Healthchecks.io.

## 11. What carried over from the earlier DigitalOcean runbook, and what was dropped

**Carried over:**

- **Access:** VPN-only access (NetBird), with no public surface and four
  independent layers.
- **Certificate:** a real domain with a Let's Encrypt certificate through
  DNS-01 in Caddy; the A record points at the NetBird address, set to DNS
  only with a 5-minute TTL.
- **Accounts:** MFA on every account.
- **Firewalls:** a provider firewall plus firewalld `drop` with `wt0` trusted.
- **Service account:** an unprivileged account runs the app.
- **Encryption at rest:** a LUKS data volume with a manual unlock; nothing
  starts at boot; the root-owned mount point prevents an unencrypted shadow
  copy; swap lives on the encrypted volume; the LUKS header is backed up off
  the box; the passphrase is kept on paper.
- **Secrets:** they live only on the encrypted volume.
- **Host hardening:** core dumps off; security-only unattended updates with
  `reboot = never`.
- **Backups:** restic to B2 with a 30-day version lifecycle, as separate
  nightly, weekly and monthly jobs, each with a dead-man switch.
- **Monitoring:** a certificate dead-man switch.
- **Deploys:** a pre-migration snapshot and rollback.
- **Procedure:** run each job by hand once before trusting the schedule.
- **This document:** "Keeping this runbook true".

**Changed:**

- **Enrolment.** The box enrols in NetBird **by a one-off setup key in
  cloud-init**, not an interactive login. The earlier DigitalOcean box kept public SSH
  open, restricted to one address, until NetBird was up. Here public SSH
  never exists at all. That runbook's objection to setup keys was a reusable
  credential lying around and silently skipped login expiry. A single-use,
  one-day, revoked key leaves nothing reusable, and login expiry is exactly
  what a server must not have.
- **The volume** is addressed by **LUKS UUID**, not a provider by-id path.
  Vultr's documentation only promises `/dev/vdb`.

**Dropped:**

| Earlier runbook item | Why it does not apply |
|---|---|
| Postgres: `pg_dump`, password rotation in three places, major-version upgrades, disposable-Postgres drill | SQLite. The backup is SQLite's online backup API, the drill opens the copy with the app itself, and there is no server to upgrade. |
| Rootless podman and compose: image store on LUKS, subuid ranges, `XDG_RUNTIME_DIR` and `su -` traps, unprivileged port 443 sysctl, checking compose limits were applied | Two Python processes and Caddy run as native systemd units with sandboxing (`ProtectSystem=strict`, `NoNewPrivileges`, `MemoryMax`, `IPAddressDeny` for the API). No image store means no secrets baked into container configs, and nothing to encrypt beyond the data. |
| Web Push and VAPID keys | The journal has no push notifications. |
| Multi-user accounts and `user create` | Single user, one bearer token (`msj-cli create-token`). |
| GitHub deploy key, building on the box | The repository is public, and releases are built on the PC and copied over NetBird. The box has no source, Node, or build toolchain. |
| GitHub Actions continuous delivery (promotion gates, GHCR, polling `deploy-check`) | One user deploying by hand. A versioned tarball plus `msj-release install` and `rollback` gives the same artifact-not-rebuild property. The pipeline can come later if a second person deploys. |
| Squashing migrations | The app's SQLite migrations are applied by the app; there is nothing to squash before a first release. |
| DigitalOcean tag-based firewall and monitoring agent | Vultr firewall groups attach per instance. There is no Vultr agent with disk alerts; `msj-health` checks disk usage. |
| Droplet "recovery console" notes | Replaced by the Vultr web console procedure (section 5). |

## 12. Sources

Vultr facts were checked on 2026-11-11 against these pages. Prices come from
the public plan list `https://api.vultr.com/v2/plans`.

- Billing, and stopped instances still billed: https://docs.vultr.com/support/platform/billing/how-am-i-billed-for-my-servers and https://docs.vultr.com/support/platform/billing/are-stopped-instances-still-billed-on-vultr
- OS images: https://api.vultr.com/v2/os
- Firewall groups:
  - inbound only: https://docs.vultr.com/products/network/firewall-groups/faq
  - no rules means not enforced: https://docs.vultr.com/products/network/firewall-groups/provisioning
  - rules: https://docs.vultr.com/products/network/firewall-groups/management/rules
- Block Storage:
  - provisioning, encrypted at rest: https://docs.vultr.com/products/storage/block-storage/provisioning
  - FAQ (sizes, attach, not in backups): https://docs.vultr.com/products/storage/block-storage/faq
  - device names: https://docs.vultr.com/products/storage/block-storage/mount/linux
  - attach: https://docs.vultr.com/products/storage/block-storage/management/attach-instances
  - NVMe regions: https://api.vultr.com/v2/regions
- VPC: https://docs.vultr.com/products/compute/instances/cloud-compute/networking/vpc
- Automatic backups (boot disk only, +20%): https://docs.vultr.com/products/storage/backups/faq and https://docs.vultr.com/vps-automatic-backups
- Snapshots: https://docs.vultr.com/products/storage/snapshots/faq
- Account 2FA: https://docs.vultr.com/support/platform/authentication/what-is-two-factor-authentication
- API access:
  - disable: https://docs.vultr.com/platform/other/api/disable-user-api-access
  - access control: https://docs.vultr.com/platform/other/api/manage-api-access-control
  - IAM policies: https://docs.vultr.com/platform/iam/policies/how-to-create-a-policy
- IPv6 (opt-in): https://docs.vultr.com/products/compute/instances/cloud-compute/networking/ipv6
- cloud-init user-data:
  - deploying with it: https://docs.vultr.com/how-to-deploy-a-vultr-server-with-cloudinit-userdata
  - readable later: https://docs.vultr.com/products/compute/instances/cloud-compute/features/cloud-init
  - Vultr datasource: https://docs.cloud-init.io/en/25.1/reference/datasources/vultr.html
- Web console:
  - overview: https://docs.vultr.com/products/compute/instances/cloud-compute/connection/vultr-console
  - paste and keyboard: https://docs.vultr.com/vultr-web-console-faq
  - root password reset: https://docs.vultr.com/how-to-reset-the-root-password-of-a-vultr-compute-instance
- Maintenance notifications: https://docs.vultr.com/platform/other/manage-notifications
- SMTP blocked: https://docs.vultr.com/support/products/compute/why-is-smtp-blocked
- Vultr DNS and its Caddy module: https://docs.vultr.com/products/network/dns/faq and https://github.com/caddy-dns/vultr

Software:

- Caddy:
  - build: https://caddyserver.com/docs/build
  - the `tls` directive: https://caddyserver.com/docs/caddyfile/directives/tls
  - Cloudflare module: https://github.com/caddy-dns/cloudflare
- NetBird:
  - Linux install: https://docs.netbird.io/how-to/installation/linux
  - CLI: https://docs.netbird.io/how-to/cli
  - DNS: https://docs.netbird.io/how-to/manage-dns-in-your-network
- restic with B2: https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html
- SQLite:
  - backup API: https://www.sqlite.org/backup.html
  - `VACUUM INTO`: https://www.sqlite.org/lang_vacuum.html
- PyTorch CPU wheels: https://download.pytorch.org/whl/cpu/torch/ (cp314 since torch 2.9.0)
- AlmaLinux 10 (x86-64-v3, v2 build): https://almalinux.org/blog/2025-05-27-welcoming-almalinux-10/

Validated in this repository before publication:

- **shellcheck** (style level) and `bash -n`: every script.
- **`caddy validate`**: the Caddyfile, with Caddy v2.11.4 built with the
  Cloudflare module.
- **cloud-init schema**: `cloud-init.yaml`, with cloud-init 24.4 on AlmaLinux 10.
- **`systemd-analyze verify`**: every unit, on systemd 257 (AlmaLinux 10).
- **End-to-end run** in an AlmaLinux 10 systemd container against a real
  release tarball:
  - `bootstrap.sh host` and `volume`;
  - `msj-release install` (CPU-only torch; offline model load under the
    sandbox), an upgrade with pinned dependencies, and `rollback`;
  - `msj-cli create-token`;
  - the backup, verify and drill units against a local restic repository,
    including a failing drill that correctly exits non-zero;
  - `msj-health`;
  - Restore B.
- **Not testable in a container** (first exercised on the real box):
  - LUKS;
  - firewalld;
  - SELinux enforcement;
  - Caddy's systemd startup and certificate issuance (the container lacks
    `CAP_SYS_ADMIN`);
  - NetBird enrolment.
