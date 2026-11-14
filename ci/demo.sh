#!/usr/bin/env bash
# Run the steps of docs/demo.md with the gateseal command on PATH.
# Usage: ci/demo.sh WHEEL SHA256
set -euo pipefail

wheel="$1"
sha256="$2"

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

step "Create a new repository"
git init -b main gateseal-demo
cd gateseal-demo
git config user.name "gateseal demo"
git config user.email "demo@example.invalid"
printf 'def greet(name):\n    return f"Hello, {name}!"\n' > greet.py

step "Select the three capabilities and activate this worktree"
gateseal configure --capability hygiene --capability python --capability secrets \
  --compiler-wheel "$wheel" --compiler-sha256 "$sha256"
gateseal activate

step "Commit the configuration and the Python file"
git add -A
git commit -m 'Adopt gateseal'

step "Trailing whitespace stops the commit"
printf 'notes  \n' > notes.txt
git add notes.txt
refused trailing-whitespace -- git commit -m 'Add notes'
git diff
git add notes.txt
git commit -m 'Add notes'

step "Ruff stops the commit"
printf 'import os\ndef shout(name):\n    return nmae.upper()\n' > shout.py
git add shout.py
refused F821 -- git commit -m 'Add shout'
printf 'def shout(name):\n    return name.upper()\n' > shout.py
git add shout.py
git commit -m 'Add shout'

step "A secret stops the commit"
printf 'aws_key = %s%s\n' AKIA Q7XK2M9P4R8T6W3Z > deploy.env
git add deploy.env
refused aws-access-token -- git commit -m 'Add deploy settings'
printf 'aws_region = eu-central-1\n' > deploy.env
git add deploy.env
git commit -m 'Add deploy settings'

step "Inspect the result"
gateseal run --json
gateseal status
gateseal doctor
