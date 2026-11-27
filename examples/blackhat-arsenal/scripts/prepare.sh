#!/usr/bin/env bash
set -euo pipefail

example_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_dir="${example_dir}/build/acme-saas"

if [[ -e "${project_dir}" ]]; then
  echo "Refusing to overwrite ${project_dir}. Remove the build directory to regenerate it." >&2
  exit 1
fi

mkdir -p "${example_dir}/build"
cp -R "${example_dir}/project-template" "${project_dir}"
cp "${project_dir}/.env.example" "${project_dir}/.env"

git -C "${project_dir}" init --quiet
git -C "${project_dir}" config user.name "Agent-Hound Demo"
git -C "${project_dir}" config user.email "demo@example.invalid"
git -C "${project_dir}" add .
git -C "${project_dir}" commit --quiet -m "Create Arsenal demo project"

echo "Prepared isolated demo project: ${project_dir}"
echo "Tracked instruction files:"
git -C "${project_dir}" ls-files \
  CLAUDE.md \
  .claude/rules/api-conventions.md \
  .claude/rules/testing.md \
  .claude/commands/triage.md \
  .claude/skills/deploy/SKILL.md
echo "Ignored local asset:"
git -C "${project_dir}" check-ignore .env
