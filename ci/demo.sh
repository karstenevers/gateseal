#!/usr/bin/env bash
# Run the steps of docs/demo.md with gateseal and pre-commit on PATH.
# Usage: ci/demo.sh
set -euo pipefail

step() {
  printf '\n== %s\n' "$*"
}

# refused PATTERN -- COMMAND...: COMMAND must fail and print PATTERN.
refused() {
  local pattern="$1"
  shift 2
  local output
  if output="$("$@" 2>&1)"; then
    printf '%s\n' "$output"
    echo "::error::'$*' succeeded but must fail"
    exit 1
  fi
  printf '%s\n' "$output"
  if ! grep -q -- "$pattern" <<<"$output"; then
    echo "::error::'$*' failed without printing '$pattern'"
    exit 1
  fi
}

root="$(mktemp -d "${RUNNER_TEMP:-$(mktemp -d)}/demo.XXXXXX")"
cd "$root"

step "Create the repository and adopt the hygiene checks"
git init -b main gateseal-demo
cd gateseal-demo
git config user.name "gateseal demo"
git config user.email "demo@example.invalid"
gateseal configure --capability hygiene
pre-commit install
git add -A
git commit -m 'Adopt gateseal'

step "Trailing whitespace stops the commit"
printf 'notes  \n' > notes.txt
git add notes.txt
refused trailing-whitespace -- git commit -m 'Add notes'

step "Stage the fix and retry"
git diff
git add notes.txt
git commit -m 'Add notes'
