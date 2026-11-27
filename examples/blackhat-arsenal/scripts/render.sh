#!/usr/bin/env bash
set -euo pipefail

example_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
capture="${example_dir}/figures/agenthound-bloodhound-clean.png"
figure_dir="${example_dir}/figures"
python_bin="${PYTHON_BIN:-python3}"

if [[ ! -f "${capture}" ]]; then
  echo "Capture the BloodHound graph as ${capture} first." >&2
  exit 1
fi

"${python_bin}" -c "import PIL" 2>/dev/null || {
  echo "Presentation overlays require Pillow: ${python_bin} -m pip install pillow" >&2
  exit 1
}

mkdir -p "${figure_dir}"
"${python_bin}" "${example_dir}/scripts/decorate_bloodhound.py" \
  "${capture}" "${figure_dir}"

echo "Decorated the authentic BloodHound capture in ${figure_dir}"
