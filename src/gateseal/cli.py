import argparse
import sys
from datetime import date
from pathlib import Path
from typing import NoReturn, cast

from gateseal import (
    __version__,
    authoring,
    checks,
    ci,
    health,
    lock,
    runtime,
    setup,
    upgrade,
)
from gateseal.diagnostics import GatesealError, Result
from gateseal.paths import resolved
from gateseal.worktree import Worktree, locate_worktree

POLICY = {
    "init": authoring.init,
    "lock": authoring.lock,
    "check": authoring.check,
}
"""The gateseal policy actions, which work on a policy directory, not a worktree."""

REVIEW = {
    "add": authoring.add,
    "pin": authoring.pin,
}
"""The gateseal policy actions that also name a component and an exact version."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise GatesealError(
            "invalid_invocation", message + ". Run gateseal --help for examples."
        )


def _options(
    parser: argparse.ArgumentParser, defaults: bool = False, repo: bool = True
) -> None:
    default = None if defaults else argparse.SUPPRESS
    if repo:
        parser.add_argument("--repo", default=default, help="Consumer repository path")
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Emit one schema-1 JSON result",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Show complete redacted child diagnostics",
    )


def _parser() -> Parser:
    parser = Parser(
        prog="gateseal",
        description="Compile reviewed repository checks and run them.",
        epilog="Examples: gateseal status; gateseal configure --help; "
        "gateseal doctor --json",
    )
    _options(parser, True)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    examples = {
        "configure": (
            "gateseal configure --capability hygiene --compiler-wheel "
            "/absolute/gateseal-0.1.0-py3-none-any.whl --compiler-sha256 SHA256"
        ),
        "check": "gateseal check --json",
        "upgrade": (
            "gateseal upgrade --policy-tag v2026.12; gateseal upgrade "
            "betterleaks=1.8.0 --reason 'Upstream issue 123' --review-by "
            "2026-12-31; gateseal upgrade betterleaks; gateseal upgrade "
            "--compiler-source URL --compiler-sha256 SHA256"
        ),
        "run": "gateseal run; gateseal --repo '/work/team repo' run --json",
        "gate": "gateseal gate; gateseal gate --base BASE_SHA --head HEAD_SHA; "
        "gateseal gate --before BEFORE_SHA --after AFTER_SHA --json",
        "setup": "gateseal setup; gateseal setup --refresh; gateseal setup --json",
        "status": "gateseal status --json",
        "doctor": "gateseal doctor --verbose",
    }
    for command, example in examples.items():
        child = commands.add_parser(command, epilog="Examples: " + example)
        _options(child)
        if command == "configure":
            child.add_argument("--capability", action="append")
            child.add_argument(
                "--policy", help="bundled, or a Git policy repository URL"
            )
            child.add_argument("--policy-commit", help="Full commit ID of a Git policy")
            child.add_argument("--policy-path", help="Policy directory, default .")
            child.add_argument("--policy-tag", help="Tag that must name the commit")
            child.add_argument("--compiler-wheel")
            child.add_argument("--compiler-source")
            child.add_argument("--compiler-sha256")
            child.add_argument(
                "--ci", choices=["github"], help="Write the CI provider's workflow"
            )
        elif command == "setup":
            child.add_argument(
                "--refresh",
                action="store_true",
                help="Also install the launcher in the existing clone at --repo",
            )
        elif command == "gate":
            for flag, about in GATE_SCOPES.items():
                child.add_argument(flag, help=about)
        elif command == "upgrade":
            child.add_argument(
                "components",
                nargs="*",
                metavar="COMPONENT[=VERSION]",
                help="Component to move to the policy's pin, or to VERSION",
            )
            child.add_argument(
                "--policy", help="bundled, or the Git policy repository to adopt"
            )
            child.add_argument("--policy-commit", help="Commit the tag must name")
            child.add_argument("--policy-path", help="Policy directory")
            child.add_argument("--policy-tag", help="Release tag to adopt")
            child.add_argument("--compiler-wheel")
            child.add_argument("--compiler-source")
            child.add_argument("--compiler-sha256")
            child.add_argument("--reason", help="Why a Version Exception is needed")
            child.add_argument(
                "--review-by",
                type=date.fromisoformat,
                help="Date to review a Version Exception, such as 2026-12-31",
            )
    policy = commands.add_parser(
        "policy",
        help="Start a team policy, review and pin tool versions, lock and check it",
        epilog="Examples: gateseal policy init team-policy/policy; "
        "gateseal policy add betterleaks 1.10.0; gateseal policy pin betterleaks "
        "1.10.0; gateseal policy lock; gateseal policy check --json",
    )
    _policy_actions(policy)
    return parser


def _policy_actions(policy: argparse.ArgumentParser) -> None:
    actions = policy.add_subparsers(dest="action", required=True)
    for action in (*POLICY, *REVIEW):
        child = actions.add_parser(action)
        _options(child, repo=False)
        if action in REVIEW:
            child.add_argument("tool", help="Component, such as betterleaks")
            child.add_argument("version", help="Exact version, such as 1.10.0")
        if action == "init":
            child.add_argument("directory", help="New or empty policy directory")
        else:
            child.add_argument(
                "directory", nargs="?", default=".", help="Policy directory, default ."
            )


GATE_SCOPES = {
    "--base": "Base commit of a pull request or merge group",
    "--head": "Head commit of a pull request or merge group",
    "--before": "Commit a push replaced",
    "--after": "Commit a push left its ref at",
}
"""gate's revision flags, in pairs: --base and --head, or --before and --after."""


def _operation(arguments: argparse.Namespace) -> Result:
    command = cast(str | None, arguments.command) or "status"
    if command == "policy":
        return _policy(arguments)
    requested = Path(cast(str | None, arguments.repo) or ".").absolute()
    if command == "setup":
        return setup.run(requested if arguments.refresh else None)
    worktree = locate_worktree(requested)
    if command == "configure":
        return _configure(worktree, arguments)
    if command == "upgrade":
        return _upgrade(worktree, arguments)
    if command == "status":
        return health.status(worktree)
    if command == "doctor":
        return health.doctor(worktree)
    return _locked(command, worktree, arguments)


def _configure(worktree: Worktree, arguments: argparse.Namespace) -> Result:
    with runtime.operation_lock(runtime.preparation_directory(worktree)):
        changed, notes = lock.configure(
            worktree.root,
            worktree.git,
            worktree.preparation,
            arguments.capability,
            lock.PolicyRequest(
                arguments.policy,
                arguments.policy_commit,
                arguments.policy_path,
                arguments.policy_tag,
            ),
            arguments.compiler_wheel,
            arguments.compiler_source,
            arguments.compiler_sha256,
            arguments.ci,
        )
    return Result(
        "configure",
        "configured",
        "Tracked policy is configured.",
        changed=changed,
        next_steps=lock.review_steps(notes),
        notes=notes,
    )


def _locked(command: str, worktree: Worktree, arguments: argparse.Namespace) -> Result:
    """Run a command that needs a Lock that agrees with the tracked files."""
    locked = health.load(worktree)
    if command == "check":
        notes = lock.describe(locked)
        return Result(
            command,
            "consistent",
            "Manifest, lock, and native policy agree.",
            next_steps=lock.review_steps(notes),
            notes=notes,
        )
    if command == "run":
        return checks.run(locked)
    return checks.gate(worktree, locked, _scope(worktree, arguments))


def _policy(arguments: argparse.Namespace) -> Result:
    action = cast(str, arguments.action)
    directory = resolved(cast(str, arguments.directory))
    if action in REVIEW:
        return REVIEW[action](
            directory, cast(str, arguments.tool), cast(str, arguments.version)
        )
    return POLICY[action](directory)


def _upgrade(worktree: Worktree, arguments: argparse.Namespace) -> Result:
    policy = lock.PolicyRequest(
        arguments.policy,
        arguments.policy_commit,
        arguments.policy_path,
        arguments.policy_tag,
    )
    compiler = (
        arguments.compiler_wheel,
        arguments.compiler_source,
        arguments.compiler_sha256,
    )
    return upgrade.run(
        worktree,
        upgrade.Request(
            None if policy == lock.PolicyRequest() else policy,
            compiler if any(compiler) else None,
            upgrade.components(cast(list[str], arguments.components)),
            arguments.reason,
            arguments.review_by,
        ),
    )


def _scope(worktree: Worktree, arguments: argparse.Namespace) -> ci.Scope | None:
    """Plan the gate's scope from at most one complete pair of revision flags."""
    given = cast(dict[str, str | None], vars(arguments))
    change, push = (given["base"], given["head"]), (given["before"], given["after"])
    pairs = [pair for pair in (change, push) if pair != (None, None)]
    if len(pairs) > 1 or any(None in pair for pair in pairs):
        raise GatesealError(
            "invalid_invocation",
            "Pass --base with --head, or --before with --after, or neither pair "
            "to check every file.",
        )
    base, head = change
    before, after = push
    # pytestmut: off[bool.and_or] -- the check above sets a pair or neither
    if base is not None and head is not None:
        return ci.change_scope(worktree, base, head)
    # pytestmut: on
    # pytestmut: off[bool.and_or] -- the check above sets a pair or neither
    if before is not None and after is not None:
        return ci.push_scope(worktree, before, after)
    # pytestmut: on
    return ci.full_scope()


def _command_name(raw: list[str]) -> str:
    """Name the command that raw asks for, as the Result of an error reports it."""
    command = next(
        (
            a
            for a in raw
            if a
            in {
                "configure",
                "check",
                "upgrade",
                "run",
                "gate",
                "status",
                "doctor",
                "setup",
                "policy",
            }
        ),
        "status",
    )
    if command == "policy":
        action = raw[raw.index("policy") + 1 :][:1]
        command = " ".join(["policy", *(a for a in action if a in POLICY | REVIEW)])
    return command


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    command = _command_name(raw)
    as_json = "--json" in raw
    # pytestmut: off[cmp.membership] -- a Result of a parse error has no details
    full = "--verbose" in raw
    # pytestmut: on
    try:
        arguments = _parser().parse_args(raw)
        as_json, full = bool(arguments.json), bool(arguments.verbose)
        result = _operation(arguments)
    except GatesealError as exc:
        result = Result(
            command,
            exc.code,
            str(exc),
            exc.exit_code,
            next_steps=(exc.next_step,) if exc.next_step else (),
        )
    except OSError as exc:
        result = Result(command, "filesystem_error", str(exc), 4)
    except KeyboardInterrupt:
        result = Result(
            command, "interrupted", "Operation interrupted. Retry the same command.", 4
        )
    print(result.render(as_json, full))
    return result.exit_code
