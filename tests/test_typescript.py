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
from installation import clone, process, success
from team_policy import URL, environment, git, publish
from typescript_policy import (
    BAD,
    CLEAN,
    DEFECT,
    FORMATTED,
    NODE,
    OTHER_NODE,
    WRONG_NODE,
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


def install_of(root, env, hook):
    """Return the folder where the hook in root keeps its packages."""
    words = shlex.split(hook_entries(root)[hook]["entry"])
    script = words[words.index("/bin/sh") + 2]
    folder = script.partition("dir=$cache/gateseal/npm/")[2].partition(";")[0]
    machine = f"{os.uname().sysname}-{os.uname().machine}"
    folder = folder.replace("$(uname -s)-$(uname -m)", machine)
    return Path(env["HOME"]) / ".cache/gateseal/npm" / folder


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
        ".gateseal/eslint.json",
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
    store = "node_modules/.store/eslint@1/node_modules"
    for folder, copy in {"node_modules": "pinned", store: "of ESLint"}.items():
        (root / folder / "espree").mkdir(parents=True)
        (root / folder / "espree/index.js").write_text(f'module.exports = "{copy}";\n')
    data = {"files": [], "parser": "espree", "plugins": {}, "rules": {}}
    (root / "eslint.json").write_text(json.dumps(data))
    loader = root / "eslint-loader.mjs"
    loader.write_bytes((consumer / ".gateseal/eslint-loader.mjs").read_bytes())
    program = root / store / "eslint/bin/eslint.js"
    program.parent.mkdir(parents=True)
    program.write_text(
        f"import({json.dumps(loader.as_uri())}).then((loaded) =>"
        " console.log(loaded.default[0].languageOptions.parser));\n"
    )
    linked = root / "node_modules/.bin/eslint"
    linked.parent.mkdir()
    linked.symlink_to(program)
    assert on_node(consumer, "eslint", linked) == "pinned"


@pytest.mark.parametrize("source", ["a folder above", "NODE_PATH"])
def test_the_loader_refuses_a_package_from_outside_the_install(
    adopted, tmp_path, source
):
    consumer, env = adopted
    root = tmp_path.resolve() / "install"
    outside = tmp_path.resolve() / ("node_modules" if source != "NODE_PATH" else "far")
    (outside / "espree").mkdir(parents=True)
    (outside / "espree/index.js").write_text('module.exports = "from outside";\n')
    data = {"files": [], "parser": "espree", "plugins": {}, "rules": {}}
    (root / "node_modules/.bin").mkdir(parents=True)
    (root / "eslint.json").write_text(json.dumps(data))
    loader = root / "eslint-loader.mjs"
    loader.write_bytes((consumer / ".gateseal/eslint-loader.mjs").read_bytes())
    program = root / "node_modules/eslint/bin/eslint.js"
    program.parent.mkdir(parents=True)
    program.write_text(f"import({json.dumps(loader.as_uri())});\n")
    linked = root / "node_modules/.bin/eslint"
    linked.symlink_to(program)
    prefix = shlex.split(hook_entries(consumer)["eslint"]["entry"])[:5]
    result = process([*prefix, "node", linked], consumer, dict(env, NODE_PATH=outside))
    installed = root / "node_modules"
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"The install {installed} holds no package espree." in result.stderr
    assert "from outside" not in result.stdout + result.stderr


def test_a_program_beside_the_pinned_node_replaces_no_package(adopted):
    consumer, env = adopted
    node = Path(on_node(consumer, "prettier", "-p", "process.execPath"))
    beside = [node.with_name(name) for name in ("prettier", "eslint")]
    try:
        for program in beside:
            program.write_text(WRONG_NODE)
            program.chmod(0o755)
        stage(consumer, "src/greet.ts", BAD)
        output = commit_fixed(consumer, env, "src/greet.ts")
    finally:
        for program in beside:
            program.unlink(missing_ok=True)
    assert (consumer / "src/greet.ts").read_text() == FORMATTED
    assert "the PATH Node ran" not in output


def test_each_hook_keeps_its_packages_in_a_folder_of_its_own(adopted):
    consumer, env = adopted
    eslint = install_of(consumer, env, "eslint")
    prettier = install_of(consumer, env, "prettier")
    assert eslint.parent.name == prettier.parent.name == f"node-{NODE}"
    assert eslint != prettier
    for install, program in ((eslint, "eslint"), (prettier, "prettier")):
        assert (install / "node_modules/.bin" / program).exists()
    for package in ("@typescript-eslint/parser", "typescript"):
        assert (eslint / "node_modules" / package / "package.json").exists()
    assert not (consumer / "node_modules").exists()
    assert all("." not in path.name for path in eslint.parent.iterdir())
    assert [path.name for path in eslint.iterdir()] == ["node_modules"]
    assert not (Path(env["HOME"]) / ".npm").exists()


def test_a_later_commit_asks_the_registry_nothing(adopted):
    consumer, env = adopted
    nowhere = "http://127.0.0.1:1/"
    away = dict(env, npm_config_registry=nowhere, npm_config_offline="false")
    stage(consumer, "src/greet.ts", BAD)
    commit_fixed(consumer, away, "src/greet.ts")
    assert (consumer / "src/greet.ts").read_text() == FORMATTED


def test_the_npm_settings_of_the_user_leave_the_install_whole_and_in_place(
    adopted, tmp_path
):
    consumer, env = adopted
    settings = tmp_path / "npmrc"
    settings.write_text(
        "dry-run=true\npackage-lock-only=true\nglobal=true\nlocation=global\n"
        "omit[]=optional\nomit[]=peer\nbin-links=false\ninstall-strategy=linked\n"
        f"usage=true\ntiming=true\nlogs-dir={tmp_path}/logs\n"
    )
    (tmp_path / "logs").mkdir()
    cache = tmp_path / "cache"
    fresh = dict(
        env,
        NPM_CONFIG_USERCONFIG=str(settings),
        NPM_CONFIG_CACHE=str(tmp_path / "downloads"),
        XDG_CACHE_HOME=str(cache),
    )
    stage(consumer, "src/greet.ts", BAD)
    commit_fixed(consumer, fresh, "src/greet.ts")
    assert (consumer / "src/greet.ts").read_text() == FORMATTED
    programs = cache.glob("gateseal/npm/*/node-*/*/node_modules/.bin/*")
    assert {"eslint", "prettier"} <= {program.name for program in programs}
    assert not (tmp_path / "downloads").exists()
    assert list((tmp_path / "logs").iterdir()) == []


def test_a_folder_that_lost_its_program_takes_none_from_the_repository(adopted):
    consumer, env = adopted
    program = install_of(consumer, env, "prettier") / "node_modules/.bin/prettier"
    target = program.readlink()
    planted = consumer / "node_modules/.bin/prettier"
    planted.parent.mkdir(parents=True)
    planted.write_text("#!/bin/sh\necho 'the repository prettier ran'\n")
    planted.chmod(0o755)
    program.unlink()
    try:
        stage(consumer, "src/greet.ts", BAD)
        result, output = commit(consumer, env)
    finally:
        program.symlink_to(target)
    assert result.returncode == 1, output
    assert "exit code: 127" in output, output
    assert "No program prettier of prettier in" in output
    assert "the repository" not in output, output
    assert (consumer / "src/greet.ts").read_text() == BAD


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
    for command in ("status", "doctor", "check"):
        shown = notes(installed.cli(consumer, command, env=env))
        for tool, version in (("eslint", "10.15.0"), ("prettier", "3.10.1")):
            note = (
                f"Tool {tool} {version} for typescript: built-in, npm-package on "
                f"Node {NODE}."
            )
            assert note in shown, (command, shown)


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
    adopt(installed, consumer, env, *source)
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
    assert (other / "node_modules/.bin/eslint").exists()
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
