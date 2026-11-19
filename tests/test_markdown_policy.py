"""A team formats Markdown with Prettier from npm, as docs/add-a-tool.md shows.

A release of a team policy adds the how-to's docs capability, and gateseal
policy lock locks it against nodejs.org and the npm registry. A consumer
commits a Markdown file that Prettier wraps. The first commit installs Node
and Prettier. A node and an npm first on PATH fail, so the hook passes only on
the pinned Node.
"""

import pytest
from conftest import on_machine
from installation import clone, success
from markdown_policy import add_prettier_markdown
from team_policy import URL, environment, git, publish
from test_typescript import commit_fixed
from typescript_policy import wrong_path

pytestmark = pytest.mark.integration

LONG = (
    "# Notes\n\n"
    "Each line of this paragraph runs past the print width of eighty characters, "
    "so Prettier wraps it.\n"
)
WRAPPED = (
    "# Notes\n\n"
    "Each line of this paragraph runs past the print width of eighty characters, so\n"
    "Prettier wraps it.\n"
)
REPOSITORY_SETTINGS = {
    ".prettierignore": "*.md\n",
    ".editorconfig": "root = true\n\n[*]\nmax_line_length = 40\n",
    ".gitignore": "notes.md\n",
}


def test_a_commit_formats_markdown_with_the_how_to_s_prettier(installed, tmp_path):
    origin = tmp_path / "markdown policy" / "gateseal-policy"
    origin.mkdir(parents=True)
    git(origin, "init", "--initial-branch=main")

    def release(data):
        add_prettier_markdown(data)
        installed.cli(data, "policy", "lock", str(data))
        installed.cli(data, "policy", "check", str(data))

    with on_machine(installed):
        publish(origin, "v1", release)
    env = dict(environment(installed, origin), PATH=wrong_path(tmp_path / "wrong"))
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    installed.cli(
        consumer,
        "configure",
        *("--capability", "hygiene", "--capability", "docs"),
        *("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1"),
        env=env,
    )
    for name, text in REPOSITORY_SETTINGS.items():
        (consumer / name).write_text(text)
    success(["git", "add", "-A"], consumer)
    success(["git", "commit", "-m", "Format Markdown"], consumer, env)
    (consumer / "notes.md").write_text(LONG)
    success(["git", "add", "--force", "notes.md"], consumer)
    output = commit_fixed(consumer, env, "notes.md")
    assert (consumer / "notes.md").read_text() == WRAPPED
    assert "Format Markdown with Prettier" in output
