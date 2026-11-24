"""Hook entries of Built-in Tools gain no part that runs only on Unix.

A hook entry starts a program directly. KNOWN_UNIX_ONLY holds the parts that
entries have today, each with the change that removes it. The list can only
get shorter: a known part that no entry has any more fails the test too.
"""

import shlex

import yaml
from conftest import PIN
from gateseal.lock import compile_outputs
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy
from gateseal.tools import built_in_tools

NPM_ENTRY = "a short hook entry for each tool from npm"
EMPTY_IGNORE_FILE = "an empty ignore file of gateseal's own"
WINDOWS = "the Windows port"

KNOWN_UNIX_ONLY = {
    ("eslint", "eslint", "/bin/sh"): NPM_ENTRY,
    ("prettier", "prettier", "/bin/sh"): NPM_ENTRY,
    ("tsc", "tsc", "/bin/sh"): NPM_ENTRY,
    ("betterleaks", "betterleaks", "/dev/null"): EMPTY_IGNORE_FILE,
    ("prettier", "prettier", "/dev/null"): EMPTY_IGNORE_FILE,
    ("coverage", "coverage-erase", "env"): WINDOWS,
    ("coverage", "coverage-run", "env"): WINDOWS,
    ("coverage", "coverage-json", "env"): WINDOWS,
    ("deptry", "deptry", "env"): WINDOWS,
    ("pyright", "pyright", "env"): WINDOWS,
    ("pytestmut", "pytestmut", "env"): WINDOWS,
}
"""Each Unix-only word that a hook entry holds, by tool, hook and word.

The value names the change that removes it.
"""

SHELLS = frozenset({"sh", "ash", "bash", "dash", "ksh", "zsh", "csh", "tcsh", "fish"})


def unix_only(word):
    """Return whether word is env, a shell, or an absolute Unix path."""
    path = word.startswith("/") or word.partition("=")[2].startswith("/")
    return path or word in SHELLS or word == "env"


def refusals(entries, known):
    """Return a message for each unknown Unix-only word and each stale known case.

    entries holds (tool, hook, words) for every hook, its entry's words and then
    its args; known maps (tool, hook, word) to the change that removes it. A
    known case covers one occurrence of its word.
    """
    found, messages = set(), []
    for tool, hook, command in entries:
        for word in filter(unix_only, command):
            case = (tool, hook, word)
            if case not in known or case in found:
                messages.append(
                    f"The {hook} hook of {tool} names {word!r}, which runs only on "
                    "Unix. A hook entry must start a program directly, with no "
                    "shell, no env and no Unix path."
                )
            found.add(case)
    messages += [
        f"The {hook} hook of {tool} no longer names {word!r}. Delete its line "
        "from KNOWN_UNIX_ONLY."
        for tool, hook, word in known
        if (tool, hook, word) not in found
    ]
    return messages


def built_in_entries():
    """Return (tool, hook, words) for each hook that configure writes.

    The bundled policy's every capability selects every Built-in Tool. A hook
    of a pre-commit repository has no entry, because its repository holds it,
    but it can have args.
    """
    policy = load_bundled_policy()
    source = BundledSource(policy.release)
    chosen = resolve(parse_manifest(render(tuple(policy.capabilities), source)), policy)
    config = yaml.safe_load(
        compile_outputs(chosen, PIN, source)[".pre-commit-config.yaml"]
    )
    tools = {
        hook: name
        for name, definition in built_in_tools().items()
        for hook in definition.hooks
    }
    hooks = [hook for repository in config["repos"] for hook in repository["hooks"]]
    assert set(tools) <= {hook["id"] for hook in hooks}, (
        "the bundled policy runs every hook of every Built-in Tool"
    )
    return [
        (tools[hook["id"]], hook["id"], words(hook))
        for hook in hooks
        if hook["id"] in tools
    ]


def words(hook):
    return [*shlex.split(hook.get("entry", "")), *hook.get("args", [])]


def test_built_in_hook_entries_hold_only_known_unix_only_words():
    assert refusals(built_in_entries(), KNOWN_UNIX_ONLY) == []


def test_a_new_unix_only_word_and_a_removed_known_case_both_fail():
    """A known case covers one word of one hook, so a second one fails."""
    hooks = {
        ("shellcheck", "shellcheck"): {"entry": "csh -c 'shellcheck \"$@\"' --"},
        ("ruff", "ruff-check"): {"args": ["--config=/etc/ruff.toml"]},
        ("prettier", "prettier"): {"entry": "mise exec node -- /bin/sh -c s prettier"},
        ("prettier", "prettier-check"): {"entry": "prettier --ignore-path /dev/null"},
        ("deptry", "deptry"): {"entry": "env A=1 uv run -- env B=2 deptry ."},
    }
    entries = [(tool, hook, words(value)) for (tool, hook), value in hooks.items()]
    known = {
        ("prettier", "prettier", "/bin/sh"): NPM_ENTRY,
        ("prettier", "prettier", "/dev/null"): EMPTY_IGNORE_FILE,
        ("deptry", "deptry", "env"): WINDOWS,
    }
    reason = (
        "which runs only on Unix. A hook entry must start a program directly, with "
        "no shell, no env and no Unix path."
    )
    assert refusals(entries, known) == [
        f"The shellcheck hook of shellcheck names 'csh', {reason}",
        f"The ruff-check hook of ruff names '--config=/etc/ruff.toml', {reason}",
        f"The prettier-check hook of prettier names '/dev/null', {reason}",
        f"The deptry hook of deptry names 'env', {reason}",
        "The prettier hook of prettier no longer names '/dev/null'. Delete its line "
        "from KNOWN_UNIX_ONLY.",
    ]
