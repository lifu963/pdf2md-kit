#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

require_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is required but not installed."
    echo "Install guide: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  fi
}

sync_deps() {
  echo "[init] Syncing dependencies with uv..."
  uv sync
}

run_tests() {
  if [[ $# -gt 0 ]]; then
    echo "[init] Running selected unittest targets: $*"
    uv run python -m unittest "$@"
  else
    echo "[init] Running full unittest suite..."
    uv run python -m unittest
  fi
}

serve_app() {
  local host="${1:-127.0.0.1}"
  local port="${2:-8000}"
  echo "[init] Starting API server at http://${host}:${port}"
  # Avoid uv's Windows trampoline on `uv run uvicorn` ("Failed to canonicalize script path").
  uv run python -m uvicorn backend.api.app:create_api_app --factory --host "${host}" --port "${port}"
}

usage() {
  cat <<'EOF'
Usage: ./init.sh <command> [args]

Commands:
  setup                      Sync dependencies only.
  test [unittest targets...] Sync dependencies, then run tests.
  serve [host] [port]        Sync dependencies, then start server.
  all                        Sync dependencies, then run full tests.
  help                       Show this help message.

Examples:
  ./init.sh test
  ./init.sh test tests.architecture.test_architecture_boundaries
  ./init.sh serve
  ./init.sh serve 0.0.0.0 8000
EOF
}

main() {
  local command="${1:-test}"
  shift || true

  require_uv

  case "${command}" in
    setup)
      sync_deps
      ;;
    test)
      sync_deps
      run_tests "$@"
      ;;
    serve)
      sync_deps
      serve_app "$@"
      ;;
    all)
      sync_deps
      run_tests
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      echo "Unknown command: ${command}"
      usage
      exit 1
      ;;
  esac
}

main "$@"
