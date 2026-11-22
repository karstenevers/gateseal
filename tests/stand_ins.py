"""What an in-process test needs in place of downloads and installed tools.

configure writes a clone's tracked policy with conftest's PIN for gateseal,
and executable writes the shell script that stands in for a program.
"""

import shlex

from gateseal import lock


def configure(worktree, capabilities):
    """Write the tracked policy for capabilities with the bundled policy."""
    lock.configure(
        worktree.root,
        worktree.git,
        worktree.preparation,
        list(capabilities),
        lock.PolicyRequest(),
        None,
    )


INSTALLED = (
    'case " $* " in\n'
    '  *" version "*) echo "2026.10.19 linux-x64"; exit 0 ;;\n'
    '  *" install "*|*" install-hooks "*) exit 0 ;;\n'
    "esac\n"
)


def executable(path, text="#!/bin/sh\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class Installer:
    def __init__(self, path):
        self.path = path
        state = path.parent
        state.mkdir(parents=True)
        self._calls = state / "calls"
        self._saw = state / "saw"
        self._failures = state / "failures"
        self._output = state / "output"
        self._errors = state / "errors"
        self._status = state / "status"
        self.fail(0)
        quoted = {
            name: shlex.quote(str(value))
            for name, value in vars(self).items()
            if name.startswith("_")
        }
        executable(
            path,
            "#!/bin/sh\n"
            'if [ "$2" = version ]; then echo "2026.10.19 linux-x64"; exit 0; fi\n'
            f"printf '%s\\n' \"$*\" >> {quoted['_calls']}\n"
            f'printf \'%s\\n%s\' "$PWD" "${{MISE_DATA_DIR:-}}" > {quoted["_saw"]}\n'
            f"left=$(cat {quoted['_failures']})\n"
            '[ "$left" -gt 0 ] || exit 0\n'
            f"echo $((left - 1)) > {quoted['_failures']}\n"
            f"cat {quoted['_output']}\n"
            f"cat {quoted['_errors']} >&2\n"
            f'exit "$(cat {quoted["_status"]})"\n',
        )

    def fail(self, times, output="", status=1, errors=""):
        self._failures.write_text(str(times))
        self._output.write_text(output)
        self._errors.write_text(errors)
        self._status.write_text(str(status))

    def calls(self):
        return self._calls.read_text().splitlines() if self._calls.exists() else []

    def saw(self):
        return tuple(self._saw.read_text().split("\n"))
