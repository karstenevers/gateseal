"""What configure writes for the Built-in Tools from npm: short and portable.

Each of their hooks is gateseal's own hook with the tool's name, its
executable's name and the tool's own arguments. No generated file of such a
tool names a shell, env or a Unix path; test_portable_hook_entries checks the
hooks themselves.
"""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import PIN
from gateseal.compiler_release import NODE_PACKAGES_HOOK, NPM_TOOL_HOOK
from gateseal.lock import compile_outputs
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy
from gateseal.tools.npm_package import NpmTool

LIMIT = 200
"""The most characters of a hook's ID and arguments, joined as a command."""

UNIX_ONLY_TEXT = re.compile(r"/bin/sh|/dev/null|\benv ")
"""Text of a generated file that runs only on Unix."""


@pytest.fixture(scope="module")
def compiled():
    """The tools from npm of the bundled policy's every capability, and its outputs."""
    policy = load_bundled_policy()
    source = BundledSource(policy.release)
    chosen = resolve(parse_manifest(render(tuple(policy.capabilities), source)), policy)
    tools = [tool for tool in chosen.tools if isinstance(tool, NpmTool)]
    assert {tool.definition.name for tool in tools} >= {"eslint", "prettier", "tsc"}
    return tools, compile_outputs(chosen, PIN, source)


def hooks(compiled, hook_id):
    """Return each hook with hook_id in the native configuration, by its alias."""
    config = json.loads(compiled[1][".pre-commit-config.yaml"])
    found = [hook for repo in config["repos"] for hook in repo["hooks"]]
    return {
        hook.get("alias", hook["id"]): hook for hook in found if hook["id"] == hook_id
    }


def test_each_hook_of_a_tool_from_npm_is_one_short_command(compiled):
    tools, _ = compiled
    rendered = hooks(compiled, NPM_TOOL_HOOK)
    assert set(rendered) == {hook.id for tool in tools for hook in tool.use.hooks}
    for tool in tools:
        for hook in tool.use.hooks:
            entry = rendered[hook.id]
            start = [tool.definition.name, tool.executable.name]
            assert entry["args"] == [*start, *hook.args]
            assert entry["name"] == hook.name
            assert "entry" not in entry and "language" not in entry
            assert len(shlex.join([entry["id"], *entry["args"]])) <= LIMIT


def test_no_generated_file_of_a_tool_from_npm_names_a_shell_env_or_a_unix_path(
    compiled,
):
    tools, outputs = compiled
    names = [f".gateseal/{tool.definition.name}" for tool in tools]
    files = {
        path: content.decode()
        for path, content in outputs.items()
        if path.startswith(tuple(names))
    }
    assert len(files) > len(tools)
    for path, text in files.items():
        assert not UNIX_ONLY_TEXT.search(text), path


def test_gateseal_s_hooks_for_node_start_a_program_of_gateseal():
    root = Path(__file__).parents[1]
    declared = {
        hook["id"]: hook
        for hook in yaml.safe_load((root / ".pre-commit-hooks.yaml").read_text())
    }
    for hook_id in (NPM_TOOL_HOOK, NODE_PACKAGES_HOOK):
        assert declared[hook_id]["language"] == "python"
    assert declared[NODE_PACKAGES_HOOK]["entry"] == NODE_PACKAGES_HOOK
    assert declared[NPM_TOOL_HOOK]["require_serial"] is True
    assert "args" not in declared[NPM_TOOL_HOOK]


def test_the_npm_tool_hook_imports_no_module_of_the_python_path(tmp_path):
    """pre-commit keeps PYTHONPATH, which can name the repository."""
    root = Path(__file__).parents[1]
    declared = yaml.safe_load((root / ".pre-commit-hooks.yaml").read_text())
    [entry] = [hook["entry"] for hook in declared if hook["id"] == NPM_TOOL_HOOK]
    (tmp_path / "platform.py").write_text("raise SystemExit('planted')\n")
    env = os.environ | {"PYTHONPATH": str(tmp_path)}
    command = [*shlex.split(entry), "tool", "tool"]
    ran = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (ran.returncode, ran.stderr) == (
        127,
        "gateseal.lock does not record the pinned Node. Run gateseal configure.\n",
    )


def test_the_check_finds_unix_only_text_and_passes_a_package_s_name():
    assert UNIX_ONLY_TEXT.search("exec /bin/sh -c x")
    assert UNIX_ONLY_TEXT.search("env A=1 node")
    assert UNIX_ONLY_TEXT.search("node > /dev/null")
    assert not UNIX_ONLY_TEXT.search('"node_modules/env-paths": {}')
