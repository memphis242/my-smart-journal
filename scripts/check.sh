#!/usr/bin/env bash
# Run every quality gate in parallel and summarize the results.
#
# Usage: scripts/check.sh [options]
#   --setup        (re)create the backend venv from backend/requirements/dev.txt (hashed)
#   --web          also run the web gates (typecheck, lint, unit tests, build, audit)
#   --e2e          also run the web Playwright end-to-end tests (implies --web)
#   --offline      skip the gates that need the network (pip-audit, pnpm audit)
#   --only LIST    run only these comma-separated gates (see --list)
#   --list         print the gate names and exit
#   -h, --help     print this help
#
# Environment:
#   MSJ_BUILD_DIR  build directory for the venv, caches, logs, coverage and test output
#                  (default: /workspace/build/my-smart-journal-<branch> when /workspace/build
#                  exists, else ${TMPDIR:-/tmp}/my-smart-journal-check)
#   MSJ_VENV       backend virtualenv (default: $MSJ_BUILD_DIR/.venv)
#   PYTHON         interpreter for --setup (default: python3.14)
#   HYPOTHESIS_PROFILE  "dev" (default) or "ci" (more examples)
#   MSJ_INTEGRATION_WORKERS  pytest-xdist workers for integration tests (default: CPUs, max 16)
#
# Nothing generated is written into the source tree except web/node_modules (gitignored).
# Exit status: 0 when every selected gate passes, 1 when any fails, 2 on usage or setup errors.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="${repo_root}/backend"
web="${repo_root}/web"

usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"; }
die() {
    echo "check.sh: $*" >&2
    exit 2
}

backend_gates=(ruff format mypy basedpyright deptry lock scripts audit tests integration)
web_gates=(web-typecheck web-lint web-test web-build web-audit)
network_gates=(audit web-audit)

setup=0 with_web=0 with_e2e=0 offline=0 only=""
while (($#)); do
    case "$1" in
        --setup) setup=1 ;;
        --web) with_web=1 ;;
        --e2e) with_web=1 with_e2e=1 ;;
        --offline) offline=1 ;;
        --only)
            (($# >= 2)) || die "--only needs a comma-separated list of gates (see --list)"
            only="$2"
            shift
            ;;
        --list)
            printf '%s\n' "${backend_gates[@]}" "${web_gates[@]}" web-e2e
            exit 0
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *) die "unknown option '$1' (see --help)" ;;
    esac
    shift
done

if [[ -z "${MSJ_BUILD_DIR:-}" ]]; then
    if [[ -d /workspace/build ]]; then
        branch="$(git -C "${repo_root}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo check)"
        MSJ_BUILD_DIR="/workspace/build/my-smart-journal-${branch//\//-}"
    else
        MSJ_BUILD_DIR="${TMPDIR:-/tmp}/my-smart-journal-check"
    fi
fi
build="${MSJ_BUILD_DIR}"
venv="${MSJ_VENV:-${build}/.venv}"
logs="${build}/check-logs"
mkdir -p "${build}" "${logs}" "${build}/pytest"

# Select gates.
selected=()
if [[ -n "${only}" ]]; then
    IFS=',' read -r -a selected <<<"${only}"
    known=" ${backend_gates[*]} ${web_gates[*]} web-e2e "
    for gate in "${selected[@]}"; do
        [[ "${known}" == *" ${gate} "* ]] || die "unknown gate '${gate}' (see --list)"
    done
else
    selected=("${backend_gates[@]}")
    ((with_web)) && selected+=("${web_gates[@]}")
    ((with_e2e)) && selected+=(web-e2e)
fi
if ((offline)); then
    kept=()
    for gate in "${selected[@]}"; do
        [[ " ${network_gates[*]} " == *" ${gate} "* ]] || kept+=("${gate}")
    done
    selected=("${kept[@]}")
fi
((${#selected[@]})) || die "no gates selected"

needs_python=0 needs_web=0
for gate in "${selected[@]}"; do
    case "${gate}" in
        web-*) needs_web=1 ;;
        *) needs_python=1 ;;
    esac
done

# Backend environment: created only from the hashed lock export.
if ((setup)); then
    python="${PYTHON:-python3.14}"
    command -v "${python}" >/dev/null || die "--setup: '${python}' not found; set PYTHON"
    echo "Creating ${venv} from backend/requirements/dev.txt"
    rm -rf "${venv}"
    "${python}" -m venv "${venv}"
    "${venv}/bin/python" -m pip install --quiet --disable-pip-version-check \
        --cache-dir "${build}/pip-cache" --require-hashes --no-deps \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r "${backend}/requirements/dev.txt" ||
        die "--setup: installing backend/requirements/dev.txt into ${venv} failed (output above)"
    "${venv}/bin/python" -m pip install --quiet --disable-pip-version-check --no-deps \
        --no-build-isolation --no-index -e "${backend}" ||
        die "--setup: installing the backend package into ${venv} failed (output above)"
fi
if ((needs_python)) && [[ ! -x "${venv}/bin/python" ]]; then
    die "no backend venv at ${venv}; run scripts/check.sh --setup (or set MSJ_VENV)"
fi

export PYTHONDONTWRITEBYTECODE=1
export RUFF_CACHE_DIR="${build}/ruff-cache"
export UV_CACHE_DIR="${build}/uv-cache"
export MSJ_HYPOTHESIS_DIR="${build}/hypothesis"
export MSJ_WEB_BUILD_ROOT="${build}/web"
bin="${venv}/bin"

run_gate() {
    local gate="$1"
    case "${gate}" in
        ruff) cd "${backend}" && "${bin}/ruff" check src tests ;;
        format) cd "${backend}" && "${bin}/ruff" format --check src tests ;;
        mypy) cd "${backend}" && "${bin}/mypy" --cache-dir "${build}/mypy-cache" ;;
        basedpyright) cd "${backend}" && "${bin}/basedpyright" --pythonpath "${bin}/python" ;;
        deptry) cd "${backend}" && "${bin}/deptry" src ;;
        lock) UV="${bin}/uv" "${repo_root}/scripts/lock.sh" --check ;;
        scripts)
            cd "${repo_root}"
            "${bin}/shellcheck" scripts/*.sh
            "${bin}/actionlint" -shellcheck "${bin}/shellcheck" .github/workflows/*.yml
            ;;
        audit)
            cd "${backend}"
            local status=0 file
            for file in requirements/deploy.txt requirements/dev.txt; do
                echo "== pip-audit ${file}"
                # OSV also covers torch's +cpu build, which the PyPI service cannot look up;
                # --strict fails on any package that could not be audited.
                "${bin}/pip-audit" --strict --vulnerability-service osv --require-hashes --disable-pip \
                    --progress-spinner off --cache-dir "${build}/pip-audit-cache" \
                    -r "${file}" || status=1
            done
            return "${status}"
            ;;
        tests)
            cd "${backend}"
            rm -rf "${build}/coverage/unit"
            mkdir -p "${build}/coverage/unit"
            COVERAGE_FILE="${build}/coverage/unit/.coverage" "${bin}/python" -m pytest -q \
                -n auto --basetemp="${build}/pytest/unit" --cov --cov-report=term \
                --cov-report="xml:${build}/coverage/unit/coverage.xml"
            ;;
        integration)
            # Each test worker runs its own serve and worker processes; stay within the CPUs.
            local workers="${MSJ_INTEGRATION_WORKERS:-$(nproc)}"
            ((workers > 16)) && workers=16
            cd "${backend}" &&
                "${bin}/python" -m pytest -q -n "${workers}" -m integration \
                    --basetemp="${build}/pytest/integration" tests/integration
            ;;
        web-typecheck) cd "${web}" && pnpm typecheck ;;
        web-lint) cd "${web}" && pnpm lint ;;
        web-test) cd "${web}" && pnpm test ;;
        web-build) cd "${web}" && pnpm build ;;
        web-audit)
            cd "${web}"
            # Shipped (runtime) dependencies gate the result. Build and lint tooling only runs
            # on trusted input, so its advisories are listed in the log for triage.
            echo "== pnpm audit (all dependencies, informational)"
            pnpm audit || true
            echo "== pnpm audit --prod (gating)"
            pnpm audit --prod
            ;;
        web-e2e) cd "${web}" && pnpm e2e ;;
        *) die "internal: no command for gate '${gate}'" ;;
    esac
}

if ((needs_web)); then
    command -v pnpm >/dev/null || die "pnpm not found; install pnpm 12 (see web/README.md)"
    echo "Installing web dependencies (pnpm install --frozen-lockfile; log: ${logs}/web-install.log)"
    (cd "${web}" && pnpm install --frozen-lockfile) >"${logs}/web-install.log" 2>&1 ||
        die "pnpm install failed; see ${logs}/web-install.log"
fi

echo "Running ${#selected[@]} gates in parallel; logs in ${logs}"
declare -A pids=()
for gate in "${selected[@]}"; do
    (
        started=${SECONDS}
        set +e
        (set -e && run_gate "${gate}") >"${logs}/${gate}.log" 2>&1
        status=$?
        echo "${status} $((SECONDS - started))" >"${logs}/${gate}.status"
        exit "${status}"
    ) &
    pids["${gate}"]=$!
done

failed=()
for gate in "${selected[@]}"; do
    wait "${pids[${gate}]}" || true
done

printf '\n%-6s %-14s %6s  %s\n' RESULT GATE SECONDS LOG
for gate in "${selected[@]}"; do
    read -r status seconds <"${logs}/${gate}.status" || { status=99 seconds="?"; }
    if [[ "${status}" == 0 ]]; then
        result=PASS
    else
        result=FAIL
        failed+=("${gate}")
    fi
    printf '%-6s %-14s %6s  %s\n' "${result}" "${gate}" "${seconds}" "${logs}/${gate}.log"
done
if ((offline)); then
    echo "(offline: skipped ${network_gates[*]})"
fi

if ((${#failed[@]})); then
    for gate in "${failed[@]}"; do
        printf '\n----- %s: last 25 lines of %s -----\n' "${gate}" "${logs}/${gate}.log"
        tail -n 25 "${logs}/${gate}.log"
    done
    printf '\ncheck.sh: %d of %d gates failed: %s\n' "${#failed[@]}" "${#selected[@]}" \
        "${failed[*]}" >&2
    exit 1
fi
printf '\ncheck.sh: all %d gates passed\n' "${#selected[@]}"
