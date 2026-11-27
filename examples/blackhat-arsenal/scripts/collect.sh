#!/usr/bin/env bash
set -euo pipefail

example_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_dir="${example_dir}/build/acme-saas"
output_dir="${example_dir}/output"
agenthound_bin="${AGENTHOUND_BIN:-agenthound}"
isolated_agent_config="${example_dir}/build/empty-agent-config"

if [[ ! -d "${project_dir}/.git" ]]; then
  echo "Run scripts/prepare.sh first." >&2
  exit 1
fi

mkdir -p "${output_dir}" "${isolated_agent_config}"

scan_command=(
  "${agenthound_bin}" scan "${project_dir}"
  --scope workspace
  --discover-mcp
  --mcp-timeout 30
  -o "${output_dir}/agenthound-representative.json"
  -v
)

if command -v bwrap >/dev/null 2>&1; then
  user_home="$(getent passwd "$(id -u)" | cut -d: -f6)"
  bubblewrap_args=(bwrap --bind / / --dev-bind /dev /dev --proc /proc)
  for config_dir in \
    "${user_home}/.codex" \
    "${user_home}/.vscode" \
    "${user_home}/.cursor" \
    "${user_home}/.codeium" \
    "${user_home}/.copilot" \
    "${user_home}/.config/Claude"; do
    if [[ -d "${config_dir}" ]]; then
      bubblewrap_args+=(--bind "${isolated_agent_config}" "${config_dir}")
    fi
  done
  "${bubblewrap_args[@]}" env \
    CLAUDE_CONFIG_DIR="${example_dir}/claude-config" \
    SUPABASE_ACCESS_TOKEN="demo-token-not-valid" \
    SENTRY_AUTH_TOKEN="demo-token-not-valid" \
    "${scan_command[@]}"
else
  echo "bwrap not found; unrelated user-level agent configs may appear in the complete export." >&2
  CLAUDE_CONFIG_DIR="${example_dir}/claude-config" \
    SUPABASE_ACCESS_TOKEN="demo-token-not-valid" \
    SENTRY_AUTH_TOKEN="demo-token-not-valid" \
    "${scan_command[@]}"
fi

echo "Representative graph: ${output_dir}/agenthound-representative.json"
echo "Import it into BloodHound, capture the graph, then run scripts/render.sh."
