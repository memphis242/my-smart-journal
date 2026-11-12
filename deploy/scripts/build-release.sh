#!/usr/bin/env bash
#
# Build a release tarball ON THE WORKSTATION (not the VPS). It contains:
#   RELEASE        id, commit, build time, Caddy version
#   wheel/         the backend wheel (no dependencies; the box resolves them)
#   web/           the built web bundle (pnpm build)
#   caddy/caddy    Caddy with the Cloudflare DNS module (xcaddy, in podman)
#   deploy/        this directory: scripts, units, Caddyfile, env templates
#
#   deploy/scripts/build-release.sh [--allow-dirty]
#
# Output: $MSJ_RELEASE_OUT (default /workspace/build/msj-releases, or
# ~/.cache/msj-releases where /workspace/build does not exist). Nothing is
# written inside the source tree.
#
# Requires: git, python3.14, pnpm (Node 22+), podman.
# CADDY_VERSION pins the Caddy release (default: the caddy:2-builder image's).
set -Eeuo pipefail

die() { printf 'build-release: %s\n' "$*" >&2; exit 1; }
log() { printf 'build-release: %s\n' "$*"; }

allow_dirty=0
case ${1:-} in '') ;; --allow-dirty) allow_dirty=1 ;; *) die "usage: build-release.sh [--allow-dirty]" ;; esac

repo=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel) || die "not inside the git repository"
cd "$repo"
for tool in python3.14 pnpm podman; do
  command -v "$tool" >/dev/null || die "$tool is not on PATH; install it (see web/README.md for pnpm and Node)"
done
if [[ -n $(git status --porcelain) ]]; then
  (( allow_dirty )) || die "the working tree has uncommitted changes; commit them (a release must be reproducible from its commit) or pass --allow-dirty for a test build"
fi

commit=$(git rev-parse HEAD)
id="$(date -u +%Y%m%dT%H%MZ)-$(git rev-parse --short=10 HEAD)"
(( allow_dirty )) && [[ -n $(git status --porcelain) ]] && id="$id-dirty"

if [[ -n ${MSJ_RELEASE_OUT:-} ]]; then out=$MSJ_RELEASE_OUT
elif [[ -d /workspace/build ]]; then out=/workspace/build/msj-releases
else out=${XDG_CACHE_HOME:-$HOME/.cache}/msj-releases
fi
work="$out/work-$id"
stage="$work/stage"
mkdir -p "$stage/wheel" "$stage/caddy"
log "building $id in $work"

log "backend wheel"
python3.14 -m pip wheel --quiet --no-deps --wheel-dir "$stage/wheel" ./backend

log "web bundle"
(cd web && pnpm install --frozen-lockfile --silent && MSJ_WEB_BUILD_ROOT="$work/web-build" pnpm build >/dev/null)
[[ -f $work/web-build/dist/index.html ]] || die "pnpm build produced no $work/web-build/dist/index.html"
cp -a "$work/web-build/dist" "$stage/web"

log "caddy with the Cloudflare DNS module"
podman run --rm -v "$stage/caddy:/out:Z" docker.io/library/caddy:2-builder \
  xcaddy build ${CADDY_VERSION:+"$CADDY_VERSION"} --with github.com/caddy-dns/cloudflare --output /out/caddy \
  >"$work/caddy-build.log" 2>&1 || die "xcaddy build failed; see $work/caddy-build.log"
"$stage/caddy/caddy" list-modules | grep -q '^dns.providers.cloudflare' || die "the built Caddy lacks dns.providers.cloudflare"
caddy_version=$("$stage/caddy/caddy" version | cut -d' ' -f1)

log "hashed dependency lock"
scripts/lock.sh --check || die "backend/uv.lock or its exports are out of date; run scripts/lock.sh and commit"
install -D -m 644 backend/requirements/deploy.txt "$stage/requirements/deploy.txt"

cp -a deploy "$stage/deploy"
cat >"$stage/RELEASE" <<EOF
id=$id
commit=$commit
built=$(date -u +%Y-%m-%dT%H:%M:%SZ)
caddy=$caddy_version
EOF

tarball="$out/msj-release-$id.tar.gz"
tar -czf "$tarball" -C "$stage" .
rm -rf -- "$work"
log "wrote $tarball ($(du -h "$tarball" | cut -f1))"
log "sha256 $(sha256sum "$tarball" | cut -d' ' -f1)"
log "next: scp $tarball root@msj-host.netbird.cloud:/root/   (RUNBOOK 'Deploying a new version')"
