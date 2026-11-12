#!/usr/bin/env bash
# Regenerate or verify the backend dependency lock.
#
#   scripts/lock.sh            re-resolve backend/uv.lock and re-export requirements/*.txt
#   scripts/lock.sh --upgrade  same, upgrading every package to its newest allowed version
#   scripts/lock.sh --check    fail if uv.lock or an export is out of date (no changes made)
#
# backend/pyproject.toml is the source of truth; never edit the lock or exports by hand.
# Requires uv on PATH or UV=/path/to/uv.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="${repo_root}/backend"
uv_bin="${UV:-uv}"
if ! command -v "${uv_bin}" >/dev/null 2>&1; then
    echo "lock.sh: uv not found (looked for '${uv_bin}'); install uv or set UV=/path/to/uv" >&2
    exit 2
fi

mode="update"
case "${1:-}" in
    "") ;;
    --upgrade) mode="upgrade" ;;
    --check) mode="check" ;;
    *)
        echo "lock.sh: unknown argument '$1'; use --upgrade or --check" >&2
        exit 2
        ;;
esac

# Each export: output file, then the uv export selection that produces it.
exports=(
    "requirements/deploy.txt|--no-dev --extra semantic --group build"
    "requirements/dev.txt|--all-extras --group build"
)

export_to() {
    local target="$1" selection="$2"
    # shellcheck disable=SC2086 # selection is a fixed list of flags
    "${uv_bin}" export --frozen --no-emit-project --format requirements-txt --quiet \
        ${selection} --output-file "${target}"
}

cd "${backend}"
case "${mode}" in
    check)
        if ! "${uv_bin}" lock --check --quiet; then
            echo "lock.sh: backend/uv.lock does not match backend/pyproject.toml; run scripts/lock.sh" >&2
            exit 1
        fi
        scratch="$(mktemp -d)"
        trap 'rm -rf "${scratch}"' EXIT
        stale=0
        for entry in "${exports[@]}"; do
            target="${entry%%|*}"
            mkdir -p "${scratch}/$(dirname "${target}")"
            export_to "${scratch}/${target}" "${entry#*|}"
            # The header records the command line, which names the output path; compare the rest.
            if ! diff -q <(grep -v '^#    uv export' "${scratch}/${target}") \
                <(grep -v '^#    uv export' "${target}") >/dev/null; then
                echo "lock.sh: backend/${target} is stale; run scripts/lock.sh" >&2
                stale=1
            fi
        done
        exit "${stale}"
        ;;
    upgrade) "${uv_bin}" lock --upgrade ;;
    update) "${uv_bin}" lock ;;
esac
for entry in "${exports[@]}"; do
    export_to "${entry%%|*}" "${entry#*|}"
done
echo "Updated backend/uv.lock and $(printf '%s ' "${exports[@]%%|*}")"
