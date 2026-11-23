"""ESLint and Prettier check TypeScript on the policy's Node: typescript.

A consumer selects the bundled policy's typescript capability and commits.
The first commit of a TypeScript file installs Node from nodejs.org and the
pinned packages from the npm registry. Every commit runs with a node and an
npm first on PATH that fail, so a hook passes only on the pinned Node.
"""

import json
import os
import shlex
import tomllib
from pathlib import Path

import pytest
from conftest import on_machine
from gateseal.policy import bundled_root
from gateseal.tools.node import FINISH, Tree, read_node
from gateseal.validation import canonical, sha
from installation import clone, process, success
from team_policy import URL, environment, git, publish
from typescript_policy import (
    BAD,
    CLEAN,
    DEFECT,
    FORMATTED,
    NODE,
    OTHER_NODE,
    pin_other_node,
    widen_prettier,
    wrong_path,
)

pytestmark = pytest.mark.integration


def untracked(root):
    """Return each file that Git does not track yet, by name, with its bytes."""
    names = success(["git", "ls-files", "-o", "--exclude-standard"], root).stdout
    return {name: (root / name).read_bytes() for name in names.split()}


def notes(result):
    return [d["message"] for d in result["diagnostics"]]


def hook_entries(root):
    config = json.loads((root / ".pre-commit-config.yaml").read_bytes())
    return {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}


def on_node(root, hook, *arguments):
    """Return what node prints for arguments on the Node of the hook in root."""
    prefix = shlex.split(hook_entries(root)[hook]["entry"])[:5]
    assert prefix[-1] == "--"
    return success([*prefix, "node", *arguments], root).stdout.strip()


def script_of(root, hook):
    words = shlex.split(hook_entries(root)[hook]["entry"])
    return words[words.index("/bin/sh") + 2]


def install_of(root, env, hook):
    """Return the folder where the hook in root keeps its packages."""
    folder = script_of(root, hook).partition("dir=$cache/gateseal/npm/")[2]
    machine = f"{os.uname().sysname}-{os.uname().machine}"
    folder = folder.partition(";")[0].replace("$(uname -s)-$(uname -m)", machine)
    return Path(env["HOME"]) / ".cache/gateseal/npm" / folder


def program_of(root, env, hook):
    """Return the program that the hook in root starts on its Node."""
    program = script_of(root, hook).partition('"$dir/"')[2].partition(" ")[0]
    return install_of(root, env, hook) / program


def started_node(root, hook):
    """Return the version of the Node that starts the hook's tool in root."""
    return on_node(root, hook, "--version")


def stage(root, name, text):
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    success(["git", "add", "--", name], root)


def commit(root, env):
    result = process(["git", "commit", "-m", "Change"], root, env)
    return result, result.stdout + result.stderr


def commit_fixed(root, env, *names):
    """Commit: the hooks fix names, which the second commit takes unchanged."""
    result, output = commit(root, env)
    assert result.returncode == 1, output
    assert "files were modified by this hook" in output, output
    success(["git", "add", "--", *names], root)
    result, output = commit(root, env)
    assert result.returncode == 0, output
    return output


def adopt(installed, consumer, env, *flags):
    flags += ("--capability", "hygiene", "--capability", "typescript")
    installed.cli(consumer, "configure", *flags, env=env)
    (consumer / "src").mkdir()
    (consumer / "src/app.ts").write_text(CLEAN)
    success(["git", "add", "-A"], consumer)
    success(["git", "commit", "-m", "Check TypeScript"], consumer, env)
    success(["git", "tag", "adopted"], consumer)
    return consumer


@pytest.fixture(scope="module")
def module_adopted(installed, tmp_path_factory):
    """A consumer of the bundled typescript capability, and its environment."""
    root = tmp_path_factory.mktemp("typescript consumer ü").resolve()
    with on_machine(installed):
        env = dict(os.environ, PATH=wrong_path(root / "wrong node"))
        consumer = adopt(installed, clone(root / "origin", root / "consumer"), env)
    return consumer, env


@pytest.fixture
def adopted(module_adopted):
    """The adopted consumer, reset to its adoption after each test."""
    yield module_adopted
    consumer, _ = module_adopted
    success(["git", "reset", "-q", "--hard", "adopted"], consumer)
    success(["git", "clean", "-fdqx"], consumer)


def test_configure_writes_the_policy_s_eslint_data(adopted):
    consumer, _ = adopted
    rules = tomllib.loads((bundled_root() / "rules/eslint.toml").read_text())
    data = json.loads((consumer / ".gateseal/eslint.json").read_bytes())
    assert data == rules
    assert data["rules"]["@typescript-eslint/no-explicit-any"] == "error"
    committed = success(["git", "ls-files", ".gateseal"], consumer).stdout.split()
    assert committed == [
        ".gateseal/eslint-loader.mjs",
        ".gateseal/eslint-suppressions.json",
        ".gateseal/eslint.json",
        ".gateseal/eslint.package-lock.json",
        ".gateseal/prettier.package-lock.json",
        ".gateseal/prettier.toml",
    ]


def test_a_commit_runs_prettier_on_the_pinned_node(adopted):
    consumer, env = adopted
    assert started_node(consumer, "prettier") == f"v{NODE}"
    wrong = process(["node", "--version"], consumer, env)
    assert (wrong.returncode, wrong.stderr) == (97, "the PATH Node ran\n")
    stage(consumer, "src/greet.ts", BAD)
    output = commit_fixed(consumer, env, "src/greet.ts")
    assert (consumer / "src/greet.ts").read_text() == FORMATTED
    assert "the PATH Node ran" not in output


def test_a_commit_fails_on_what_typescript_eslint_refuses(adopted):
    consumer, env = adopted
    stage(consumer, "src/count.ts", DEFECT)
    result, output = commit(consumer, env)
    assert result.returncode == 1, output
    assert "src/count.ts" in output
    assert "@typescript-eslint/no-explicit-any" in output
    assert "- hook id: eslint" in output
    heads = success(["git", "rev-parse", "HEAD", "adopted"], consumer).stdout.split()
    assert heads[0] == heads[1]


def test_eslint_fixes_and_prettier_formats_without_undoing_each_other(adopted):
    consumer, env = adopted
    stage(consumer, "src/total.ts", "let total = 1\nexport { total }\n")
    output = commit_fixed(consumer, env, "src/total.ts")
    assert (consumer / "src/total.ts").read_text() == (
        "const total = 1;\nexport { total };\n"
    )
    assert "eslint" in output and "prettier" in output
    assert "Failed" not in output


REPOSITORY_SETTINGS = {
    "eslint.config.js": 'throw new Error("the repository ESLint config ran");\n',
    ".prettierrc": '{"semi": false, "singleQuote": true, "tabWidth": 8}\n',
    ".prettierignore": "*.ts\n",
    ".editorconfig": "root = true\n\n[*]\nindent_style = tab\nmax_line_length = 40\n",
    "node_modules/@typescript-eslint/parser/package.json": (
        '{"name": "@typescript-eslint/parser", "version": "8.73.0"}\n'
    ),
    "node_modules/@typescript-eslint/parser/index.js": (
        'throw new Error("the repository parser ran");\n'
    ),
    "node_modules/.bin/node": "#!/bin/sh\necho 'the repository node ran'\nexit 96\n",
    "node_modules/.bin/sh": "#!/bin/sh\necho 'the repository sh ran'\nexit 95\n",
    "node_modules/.bin/eslint": "#!/bin/sh\necho 'the repository eslint ran'\n",
    "eslint-suppressions.json": json.dumps(
        {"src/count.ts": {"@typescript-eslint/no-explicit-any": {"count": 1}}}
    ),
}


def test_the_repository_s_own_settings_and_packages_change_nothing(adopted):
    consumer, env = adopted
    for name, text in REPOSITORY_SETTINGS.items():
        (consumer / name).parent.mkdir(parents=True, exist_ok=True)
        (consumer / name).write_text(text)
        (consumer / name).chmod(0o755)
    stage(consumer, "src/greet.ts", BAD)
    output = commit_fixed(consumer, env, "src/greet.ts")
    assert (consumer / "src/greet.ts").read_text() == FORMATTED
    assert "the repository" not in output, output
    stage(consumer, "src/count.ts", DEFECT)
    result, output = commit(consumer, env)
    assert result.returncode == 1, output
    assert "@typescript-eslint/no-explicit-any" in output


def test_the_loader_takes_a_package_from_the_root_of_the_install(adopted, tmp_path):
    consumer, _ = adopted
    root = tmp_path.resolve()
    nested = "node_modules/eslint/node_modules"
    for folder, copy in {"node_modules": "pinned", nested: "of ESLint"}.items():
        (root / folder / "espree").mkdir(parents=True)
        (root / folder / "espree/index.js").write_text(f'module.exports = "{copy}";\n')
    data = {"files": [], "parser": "espree", "plugins": {}, "rules": {}}
    (root / "eslint.json").write_text(json.dumps(data))
    loader = root / "eslint-loader.mjs"
    loader.write_bytes((consumer / ".gateseal/eslint-loader.mjs").read_bytes())
    program = root / "node_modules/eslint/bin/eslint.js"
    program.parent.mkdir(parents=True)
    program.write_text(
        f"import({json.dumps(loader.as_uri())}).then((loaded) =>"
        " console.log(loaded.default[0].languageOptions.parser));\n"
    )
    assert on_node(consumer, "eslint", program) == "pinned"


def test_each_hook_keeps_its_packages_in_a_folder_of_its_own(adopted):
    consumer, env = adopted
    eslint = install_of(consumer, env, "eslint")
    prettier = install_of(consumer, env, "prettier")
    assert eslint.parent.name == prettier.parent.name == f"node-{NODE}"
    assert eslint != prettier
    assert program_of(consumer, env, "prettier").is_relative_to(prettier)
    for package in ("eslint", "@typescript-eslint/parser", "typescript"):
        assert (eslint / "node_modules" / package / "package.json").exists()
    assert not (consumer / "node_modules").exists()
    assert all("." not in path.name for path in eslint.parent.iterdir())
    assert sorted(path.name for path in eslint.iterdir()) == [
        "node_modules",
        "package-lock.json",
        "package.json",
    ]
    assert not (Path(env["HOME"]) / ".npm").exists()


def test_a_later_commit_asks_the_registry_nothing(adopted):
    consumer, env = adopted
    nowhere = "http://127.0.0.1:1/"
    away = dict(env, npm_config_registry=nowhere, npm_config_offline="false")
    stage(consumer, "src/greet.ts", BAD)
    commit_fixed(consumer, away, "src/greet.ts")
    assert (consumer / "src/greet.ts").read_text() == FORMATTED


def test_a_first_install_reads_no_npm_setting_but_a_proxy_ca_or_registry(
    adopted, tmp_path
):
    """npm installs the lockfile whole, whatever else the user's .npmrc says."""
    consumer, env = adopted
    user = Path(env["HOME"]) / ".npmrc"
    user.write_text(
        "workspace=app\nworkspaces=true\ndry-run=true\npackage-lock-only=true\n"
        "global=true\nlocation=global\nomit[]=optional\nbin-links=false\n"
        f"install-strategy=linked\nignore-scripts=false\nlogs-dir={tmp_path}/logs\n"
    )
    (tmp_path / "logs").mkdir()
    cache = tmp_path / "cache"
    fresh = dict(env, XDG_CACHE_HOME=str(cache), npm_config_workspace="app")
    try:
        stage(consumer, "src/greet.ts", BAD)
        commit_fixed(consumer, fresh, "src/greet.ts")
    finally:
        user.unlink()
    assert (consumer / "src/greet.ts").read_text() == FORMATTED
    installs = list(cache.glob("gateseal/npm/*/node-*/*/node_modules"))
    assert len(installs) == 2
    assert list((tmp_path / "logs").iterdir()) == []


BROKEN = "sha512-" + "A" * 86 + "=="
"""An integrity hash that no package has."""


def first_use(consumer, env, tmp_path, tool, lockfile, program):
    """Start tool on its first use from lockfile, as policy.lock may record it."""
    tree = Tree(canonical(lockfile), program)
    lock = json.loads((bundled_root() / "policy.lock").read_bytes())
    launch = read_node(NODE, lock["node"]).launch(tree, None, tool)
    fresh = dict(env, XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = process([*launch, "--version"], consumer, fresh)
    assert (result.returncode, result.stdout) == (127, ""), result.stderr
    assert result.stderr.endswith(
        f"npm cannot install the locked packages of {tool}.\n"
    ), result.stderr
    assert not list((tmp_path / "cache").glob("gateseal/npm/*/node-*/*"))
    return result.stderr


def test_a_changed_lockfile_hash_stops_the_first_install(adopted, tmp_path):
    consumer, env = adopted
    lock = consumer / ".gateseal/prettier.package-lock.json"
    data = json.loads(lock.read_bytes())
    data["packages"]["node_modules/prettier"]["integrity"] = BROKEN
    program = "node_modules/prettier/bin/prettier.cjs"
    output = first_use(consumer, env, tmp_path, "prettier", data, program)
    assert "EINTEGRITY" in output
    assert "A complete log" not in output


def test_a_package_missing_from_the_lockfile_stops_the_first_install(adopted, tmp_path):
    consumer, env = adopted
    lock = consumer / ".gateseal/eslint.package-lock.json"
    data = json.loads(lock.read_bytes())
    del data["packages"]["node_modules/typescript"]
    first_use(
        consumer, env, tmp_path, "eslint", data, "node_modules/eslint/bin/eslint.js"
    )


def test_an_optional_package_that_npm_drops_stops_the_first_install(adopted, tmp_path):
    """npm ci leaves out an optional package whose hash differs, and exits 0."""
    consumer, env = adopted
    lock = json.loads((bundled_root() / "policy.lock").read_bytes())
    lockfile = lock["components"]["tsc"]["versions"]["7.0.2"]["lockfile"]
    platform = on_node(consumer, "prettier", "-p", 'process.platform+"-"+process.arch')
    native = f"node_modules/@typescript/typescript-{platform}"
    lockfile["packages"][native]["integrity"] = BROKEN
    program = "node_modules/typescript/bin/tsc"
    output = first_use(consumer, env, tmp_path, "tsc", lockfile, program)
    assert output.endswith(
        f"npm installed no {native}.\nnpm cannot install the locked packages of tsc.\n"
    )


def test_a_lockfile_other_than_the_hook_s_fills_no_install(adopted, tmp_path):
    """The same packages in other bytes: npm does not run, and nothing installs."""
    consumer, env = adopted
    lock = consumer / ".gateseal/prettier.package-lock.json"
    lock.write_text(json.dumps(json.loads(lock.read_bytes())))
    fresh = dict(env, XDG_CACHE_HOME=str(tmp_path / "cache"))
    words = shlex.split(hook_entries(consumer)["prettier"]["entry"])
    result = process(words, consumer, fresh)
    assert (result.returncode, result.stdout) == (127, ""), result.stderr
    assert result.stderr.endswith(
        "The lockfile is not the one that the hook names.\n"
        "npm cannot install the locked packages of prettier.\n"
    ), result.stderr
    assert "npm error" not in result.stderr
    assert not list((tmp_path / "cache").glob("gateseal/npm/*/node-*/*"))


def test_a_crlf_lockfile_without_other_platforms_packages_installs(adopted, tmp_path):
    """npm leaves out a portable package that only another platform's needs."""
    consumer, _ = adopted
    packages = {
        "": {"dependencies": {"tool": "1.0.0"}},
        "node_modules/tool": {"version": "1.0.0"},
        "node_modules/tool-aix": {"optional": True, "os": ["aix"], "version": "1.0.0"},
        "node_modules/helper": {"optional": True, "version": "1.0.0"},
    }
    lockfile = canonical({"lockfileVersion": 3, "packages": packages})
    new, final = tmp_path / "new", tmp_path / "final"
    (new / "node_modules").mkdir(parents=True)
    (new / "package-lock.json").write_bytes(lockfile.replace(b"\n", b"\r\n"))
    installed = {"packages": {"": {}, "node_modules/tool": {}}}
    (new / "node_modules/.package-lock.json").write_text(json.dumps(installed))
    on_node(consumer, "prettier", "-e", FINISH, new, sha(lockfile), final)
    assert final.is_dir()
    assert not new.exists()


def test_a_file_named_like_an_option_is_checked_as_a_file(adopted):
    consumer, env = adopted
    stage(consumer, "--weird.ts", BAD.replace("const  greeting", "var greeting"))
    result, output = commit(consumer, env)
    assert result.returncode == 1, output
    assert (consumer / "--weird.ts").read_text() == FORMATTED
    stage(consumer, "--defect.ts", DEFECT)
    result, output = commit(consumer, env)
    assert result.returncode == 1, output
    assert "--defect.ts" in output and "no-explicit-any" in output, output


def test_run_gate_and_reports_name_each_tool_and_its_node(installed, adopted):
    consumer, env = adopted
    for command, stage_name in (("run", "run"), ("gate", "ci")):
        result = installed.cli(consumer, command, env=env)
        hooks = {d["hook"]: d for d in result["diagnostics"] if d["category"] == "hook"}
        for hook in ("eslint", "prettier"):
            assert hooks[hook]["status"] == "passed", (command, hooks[hook])
            assert hooks[hook]["stage"] == stage_name
            assert hooks[hook]["node"] == NODE
        assert "node" not in hooks["check-yaml"]
    for command in ("status", "check"):
        shown = notes(installed.cli(consumer, command, env=env))
        for tool, version in (("eslint", "10.15.0"), ("prettier", "3.10.1")):
            note = (
                f"Tool {tool} {version} for typescript: built-in, npm-package on "
                f"Node {NODE}."
            )
            assert note in shown, (command, shown)


def test_eslint_that_cannot_lint_stops_run_as_a_tool_failure(installed, adopted):
    consumer, env = adopted
    parser = (
        install_of(consumer, env, "eslint") / "node_modules/@typescript-eslint/parser"
    )
    aside = parser.with_name("parser-aside")
    parser.rename(aside)
    try:
        result = installed.cli(consumer, "run", code=4, env=env)
    finally:
        aside.rename(parser)
    assert result["code"] == "tool_failed"
    hooks = {d["hook"]: d for d in result["diagnostics"] if d["category"] == "hook"}
    assert (hooks["eslint"]["status"], hooks["eslint"]["exit_code"]) == ("stopped", 2)


def test_an_excluded_path_reaches_neither_tool(installed, adopted):
    consumer, env = adopted
    manifest = consumer / "gateseal.toml"
    manifest.write_text(
        manifest.read_text() + '\n[choices.typescript]\nexclude = ["vendor"]\n'
    )
    installed.cli(consumer, "configure", env=env)
    success(["git", "add", "-A"], consumer)
    stage(consumer, "vendor/legacy.ts", BAD + DEFECT)
    result, output = commit(consumer, env)
    assert result.returncode == 0, output
    assert (consumer / "vendor/legacy.ts").read_text() == BAD + DEFECT


def test_a_commit_of_only_node_modules_files_passes_both_tools(adopted):
    consumer, env = adopted
    name = "node_modules/lib/legacy.ts"
    (consumer / name).parent.mkdir(parents=True)
    (consumer / name).write_text(BAD + DEFECT)
    success(["git", "add", "-f", "--", name], consumer)
    result, output = commit(consumer, env)
    assert result.returncode == 0, output
    assert (consumer / name).read_text() == BAD + DEFECT


PNPM = {
    "package.json": json.dumps(
        {
            "name": "shop",
            "private": True,
            "devEngines": {
                "packageManager": {"name": "pnpm", "version": "10.18.0"},
            },
        },
        indent=2,
    )
    + "\n",
    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
}


def test_both_run_in_a_pnpm_project_without_node_modules(installed, tmp_path):
    with on_machine(installed):
        env = dict(os.environ, PATH=wrong_path(tmp_path / "wrong node"))
        consumer = clone(tmp_path / "origin", tmp_path / "consumer")
        for name, text in PNPM.items():
            (consumer / name).write_text(text)
        adopt(installed, consumer, env)
        assert not (consumer / "package-lock.json").exists()
        assert not (consumer / "node_modules").exists()
        stage(consumer, "src/greet.ts", BAD)
        commit_fixed(consumer, env, "src/greet.ts")
        assert (consumer / "src/greet.ts").read_text() == FORMATTED
        stage(consumer, "src/count.ts", DEFECT)
        result, output = commit(consumer, env)
        assert result.returncode == 1, output
        assert "@typescript-eslint/no-explicit-any" in output


@pytest.fixture(scope="module")
def team(installed, tmp_path_factory):
    """A team policy whose v1 pins another Node and whose v2 widens Prettier."""
    origin = tmp_path_factory.mktemp("typescript team ü") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")

    def lock(data):
        installed.cli(data, "policy", "lock", str(data))

    with on_machine(installed):
        publish(origin, "v1", lambda data: pin_other_node(data, lock))
        publish(origin, "v2", widen_prettier)
    return origin


def test_two_repositories_with_two_node_pins_each_run_their_own(
    installed, team, adopted, tmp_path
):
    bundled, _ = adopted
    env = dict(environment(installed, team), PATH=wrong_path(tmp_path / "wrong node"))
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    source = ("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1")
    adopt(installed, consumer, dict(env, MISE_OFFLINE="0"), *source)
    assert started_node(consumer, "eslint") == f"v{OTHER_NODE}"
    assert started_node(bundled, "eslint") == f"v{NODE}"
    other = install_of(consumer, env, "eslint")
    assert other.parent.name == f"node-{OTHER_NODE}"
    assert other.name == install_of(bundled, env, "eslint").name
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["native_artifacts"]["node"]["version"] == OTHER_NODE
    stage(consumer, "src/greet.ts", BAD)
    commit_fixed(consumer, env, "src/greet.ts")
    assert (consumer / "src/greet.ts").read_text() == FORMATTED
    assert program_of(consumer, env, "eslint").is_file()
    status = notes(installed.cli(consumer, "status", env=env))
    assert (
        f"Tool eslint 10.15.0 for typescript: built-in, npm-package on Node "
        f"{OTHER_NODE}." in status
    )


def test_prettier_on_json_and_yaml_leaves_every_generated_file(
    installed, team, tmp_path
):
    env = dict(environment(installed, team), PATH=wrong_path(tmp_path / "wrong node"))
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    source = ("--policy", URL, "--policy-path", "policy", "--policy-tag", "v2")
    flags = ("--capability", "hygiene", "--capability", "typescript")
    installed.cli(consumer, "configure", *flags, *source, "--ci", "github", env=env)
    written = untracked(consumer)
    assert {".gateseal/eslint.json", ".github/workflows/gateseal.yml"} <= set(written)
    (consumer / "data.json").write_text('{"a":1,\n"b":[1,2]}\n')
    success(["git", "add", "-A"], consumer)
    commit_fixed(consumer, env, "data.json")
    assert (consumer / "data.json").read_text() == '{ "a": 1, "b": [1, 2] }\n'
    shown = success(["git", "show", "--name-only", "--format="], consumer).stdout
    assert set(shown.split()) == {*written, "data.json"}
    assert {name: (consumer / name).read_bytes() for name in written} == written
    assert installed.cli(consumer, "check", env=env)["code"] == "consistent"
