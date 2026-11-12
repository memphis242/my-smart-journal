#!/usr/bin/env bash
#
# Idempotent host setup for the journal on AlmaLinux 10. Run as root from an
# unpacked release tarball (build-release.sh), which carries this deploy/
# directory and the Caddy binary:
#
#   deploy/scripts/bootstrap.sh host     packages, firewall, sshd, users,
#                                         directories, scripts, units, Caddy,
#                                         SELinux labels, updates, core dumps
#   deploy/scripts/bootstrap.sh volume   directories, labels and swap on the
#                                         unlocked LUKS volume (after msj-unlock
#                                         --no-start, or the first luksFormat)
#
# Safe to rerun at any time; every deploy reruns `host` so the box's scripts
# and units always match the release. It never formats a disk, never touches
# secrets, and never overwrites /etc/msj/config.toml, host.env or
# healthchecks.env once they exist.
set -Eeuo pipefail
MSJ_JOB=bootstrap
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
readonly DEPLOY_DIR=${here%/scripts}
readonly BUNDLE_DIR=${DEPLOY_DIR%/deploy}
# shellcheck source=lib/common.sh
. "$here/lib/common.sh"

readonly HOSTNAME_WANTED=msj-host
readonly SWAP_MB=2048
readonly TIMERS=(msj-health.timer msj-backup.timer msj-backup-verify.timer msj-restore-drill.timer msj-cert-check.timer)

changed_caddy=0

check_os() {
  # shellcheck source=/dev/null
  . /etc/os-release
  [[ ${ID:-} == almalinux && ${VERSION_ID:-} == 10* ]] \
    || msj_die "this script targets AlmaLinux 10; found ${PRETTY_NAME:-unknown}. Adjust it before using another OS."
  [[ $(getenforce) == Enforcing ]] \
    || msj_warn "SELinux is $(getenforce), not Enforcing. Set SELINUX=enforcing in /etc/selinux/config, touch /.autorelabel and reboot before going on."
}

install_packages() {
  dnf -y -q install epel-release
  # python3.14 comes from AppStream and receives AlmaLinux security errata.
  # curl is deliberately absent: the image ships curl-minimal, which conflicts.
  dnf -y -q install firewalld cryptsetup sqlite restic python3.14 openssl tar \
    policycoreutils-python-utils dnf-automatic dnf-plugins-core util-linux
}

setup_hostname() {
  if [[ $(hostnamectl --static) != "$HOSTNAME_WANTED" ]]; then
    hostnamectl set-hostname "$HOSTNAME_WANTED"
    msj_warn "hostname set to $HOSTNAME_WANTED. It names the NetBird peer and the restic snapshot group; keep it on any replacement box."
  fi
}

setup_firewall() {
  systemctl enable --now firewalld
  firewall-cmd -q --permanent --zone=trusted --query-interface=wt0 \
    || firewall-cmd -q --permanent --zone=trusted --add-interface=wt0
  [[ $(firewall-cmd --get-default-zone) == drop ]] || firewall-cmd -q --set-default-zone=drop
  # An interface bound explicitly to another zone (e.g. public by the image's
  # NetworkManager profile) would bypass the default zone; move it.
  local zone ifaces iface
  while read -r zone; do
    [[ -z $zone || $zone == drop || $zone == trusted ]] && continue
    ifaces=$(firewall-cmd --permanent --zone="$zone" --list-interfaces)
    for iface in $ifaces; do
      firewall-cmd -q --permanent --zone=drop --change-interface="$iface"
      msj_log "moved $iface from zone $zone to drop"
    done
  done < <(firewall-cmd --get-zones | tr ' ' '\n')
  firewall-cmd -q --reload
  local active
  active=$(firewall-cmd --get-active-zones)
  grep -q '^trusted' <<<"$active" || msj_warn "wt0 is not active in zone trusted yet (is NetBird up? netbird status)"
  msj_log "firewalld active zones: $(tr '\n' ' ' <<<"$active")"
}

setup_sshd() {
  install -m 600 /dev/stdin /etc/ssh/sshd_config.d/10-msj.conf <<'EOF'
# Keys only. The box has no public inbound port; SSH arrives over NetBird.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  sshd -t || msj_die "sshd -t rejects the configuration; fix /etc/ssh/sshd_config.d/ before reloading"
  systemctl reload sshd
}

harden_host() {
  install -d /etc/systemd/coredump.conf.d
  printf '[Coredump]\nStorage=none\nProcessSizeMax=0\n' >/etc/systemd/coredump.conf.d/msj.conf
  install -d /etc/sysctl.d
  printf 'fs.suid_dumpable = 0\nvm.swappiness = 10\n' >/etc/sysctl.d/90-msj.conf
  sysctl -q -p /etc/sysctl.d/90-msj.conf
  if rpm -q abrt >/dev/null 2>&1; then
    systemctl disable --now abrtd abrt-journal-core 2>/dev/null || true
    msj_warn "abrt was installed; its services are now disabled"
  fi
  install -d /etc/systemd/journald.conf.d
  printf '[Journal]\nSystemMaxUse=500M\n' >/etc/systemd/journald.conf.d/msj.conf
  systemctl restart systemd-journald

  # Security updates daily, never an automatic reboot: a reboot locks the
  # volume and takes the journal down until someone unlocks it.
  local conf=/etc/dnf/automatic.conf
  sed -i -E 's/^upgrade_type *=.*/upgrade_type = security/; s/^apply_updates *=.*/apply_updates = yes/; s/^reboot *=.*/reboot = never/' "$conf"
  if ! grep -q '^upgrade_type = security' "$conf" || ! grep -q '^apply_updates = yes' "$conf"; then
    msj_die "could not set upgrade_type/apply_updates in $conf; edit it by hand"
  fi
  grep -q '^reboot = never' "$conf" || msj_warn "$conf has no 'reboot =' line; dnf-automatic's default is never, but check"
  systemctl enable --now dnf-automatic.timer
}

setup_users_and_dirs() {
  getent passwd "$MSJ_USER" >/dev/null \
    || useradd --system --home-dir /var/lib/msj --no-create-home --shell /usr/sbin/nologin "$MSJ_USER"
  getent passwd caddy >/dev/null \
    || useradd --system --home-dir /var/lib/caddy --no-create-home --shell /usr/sbin/nologin caddy
  install -d -m 750 -o root -g "$MSJ_USER" /etc/msj
  install -d -m 700 -o "$MSJ_USER" -g "$MSJ_USER" /var/lib/msj /var/lib/msj/hf
  install -d -m 755 /opt/msj "$MSJ_RELEASES" /etc/caddy
  # Root-owned while the volume is locked: anything that tries to write under
  # /srv/msj then fails instead of creating an unencrypted copy on the boot disk.
  install -d -m 755 -o root -g root "$MSJ_MOUNT"
}

install_files() {
  install -d -m 755 /usr/local/lib/msj
  install -m 644 "$DEPLOY_DIR/scripts/lib/common.sh" /usr/local/lib/msj/common.sh
  local f
  for f in "$DEPLOY_DIR"/scripts/msj-*; do
    install -m 755 "$f" "/usr/local/sbin/$(basename "$f")"
  done
  for f in "$DEPLOY_DIR"/systemd/*; do
    install -m 644 "$f" "/etc/systemd/system/$(basename "$f")"
  done
  install -m 644 "$DEPLOY_DIR/caddy/Caddyfile" /etc/caddy/Caddyfile
  install -m 640 -o root -g "$MSJ_USER" "$DEPLOY_DIR/env/msj.env" /etc/msj/msj.env

  if [[ ! -f /etc/msj/config.toml ]]; then
    install -m 640 -o root -g "$MSJ_USER" "$DEPLOY_DIR/config.production.toml" /etc/msj/config.toml
  elif ! cmp -s "$DEPLOY_DIR/config.production.toml" /etc/msj/config.toml; then
    msj_warn "/etc/msj/config.toml differs from this release's deploy/config.production.toml; review: diff $DEPLOY_DIR/config.production.toml /etc/msj/config.toml"
  fi
  if [[ ! -f $MSJ_HOST_ENV ]]; then
    install -m 644 "$DEPLOY_DIR/env/host.env.example" "$MSJ_HOST_ENV"
    msj_warn "created $MSJ_HOST_ENV from the example; edit MSJ_HOSTNAME, MSJ_BIND_IP and MSJ_LUKS_DEVICE"
  fi
  if [[ ! -f $MSJ_HC_ENV ]]; then
    install -m 600 "$DEPLOY_DIR/env/healthchecks.env.example" "$MSJ_HC_ENV"
    msj_warn "created $MSJ_HC_ENV from the example; paste the five Healthchecks.io ping URLs"
  fi

  local caddy_src="$BUNDLE_DIR/caddy/caddy"
  if [[ -x $caddy_src ]]; then
    if ! cmp -s "$caddy_src" /usr/local/bin/caddy; then
      install -m 755 "$caddy_src" /usr/local/bin/caddy
      changed_caddy=1
      msj_log "installed $(/usr/local/bin/caddy version | cut -d' ' -f1) to /usr/local/bin/caddy"
    fi
  else
    [[ -x /usr/local/bin/caddy ]] || msj_die "no Caddy binary at $caddy_src and none installed; build the release with deploy/scripts/build-release.sh"
  fi
  /usr/local/bin/caddy list-modules | grep -q '^dns.providers.cloudflare' \
    || msj_die "/usr/local/bin/caddy has no dns.providers.cloudflare module; rebuild it with build-release.sh"
}

setup_selinux() {
  # The swapfile needs swapfile_t or swapon fails with a bare "permission
  # denied". Everything else runs under the default labels (/opt/msj is bin_t
  # and usr_t; services run as unconfined_service_t inside their systemd
  # sandboxes).
  semanage fcontext -l | grep -q '^/srv/msj/swapfile ' \
    || semanage fcontext -a -t swapfile_t /srv/msj/swapfile
  restorecon -F /usr/local/bin/caddy
  restorecon -RF /etc/caddy /opt/msj /var/lib/msj
}

enable_units() {
  systemctl daemon-reload
  local t
  for t in "${TIMERS[@]}"; do systemctl enable --now "$t" >/dev/null; done
  # Started only by msj-unlock (msj.target), never at boot.
  systemctl disable caddy.service msj-serve.service msj-worker.service >/dev/null 2>&1 || true
  if (( changed_caddy )) && systemctl is-active --quiet caddy; then
    systemctl restart caddy
    msj_log "restarted caddy on the new binary"
  fi
}

cmd_host() {
  msj_require_root
  check_os
  install_packages
  setup_hostname
  setup_firewall
  setup_sshd
  harden_host
  setup_users_and_dirs
  install_files
  setup_selinux
  enable_units
  rpm -q netbird >/dev/null 2>&1 || msj_warn "netbird is not installed; cloud-init should have installed it (RUNBOOK step 'Deploy the instance')"
  msj_log "host ready"
}

cmd_volume() {
  msj_require_root
  msj_require_unlocked
  install -d -m 755 -o root -g root "$MSJ_MOUNT"
  install -d -m 700 -o "$MSJ_USER" -g "$MSJ_USER" "$MSJ_DATA"
  install -d -m 700 -o root -g root "$MSJ_SECRETS_DIR" "$RESTIC_CACHE_DIR"
  install -d -m 700 -o caddy -g caddy /srv/msj/caddy
  install -d -m 710 -o root -g "$MSJ_USER" "$MSJ_BACKUP_DIR"
  install -d -m 711 -o root -g root "$MSJ_RESTORE_DIR"

  if [[ ! -f $MSJ_SWAPFILE ]]; then
    # dd, not fallocate: swapon rejects a file with unwritten extents. On the
    # encrypted volume because swapped pages are process memory verbatim.
    dd if=/dev/zero of="$MSJ_SWAPFILE.partial" bs=1M count="$SWAP_MB" status=none
    chmod 600 "$MSJ_SWAPFILE.partial"
    mv "$MSJ_SWAPFILE.partial" "$MSJ_SWAPFILE"
    restorecon "$MSJ_SWAPFILE"
    mkswap -q "$MSJ_SWAPFILE"
  fi
  restorecon -RF "$MSJ_MOUNT"
  swapon --show=NAME --noheadings | grep -qx "$MSJ_SWAPFILE" || swapon "$MSJ_SWAPFILE"

  local f missing=()
  for f in app.env caddy.env backup.env restic-password; do
    [[ -f $MSJ_SECRETS_DIR/$f ]] || missing+=("$f")
  done
  (( ${#missing[@]} == 0 )) || msj_warn "secrets still to write in $MSJ_SECRETS_DIR: ${missing[*]} (RUNBOOK 'Secrets and configuration')"
  msj_log "volume ready: $(df -h --output=size,avail "$MSJ_MOUNT" | tail -1 | tr -s ' ')"
}

case ${1:-} in
  host) cmd_host ;;
  volume) cmd_volume ;;
  *) msj_die "usage: bootstrap.sh host|volume" ;;
esac
