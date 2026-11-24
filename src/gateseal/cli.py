import argparse
import os
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, NoReturn, cast

from gateseal import (
    __version__,
    authoring,
    checks,
    ci,
    fetch,
    health,
    lock,
    setup,
    upgrade,
)
from gateseal.ci_providers import PROVIDERS, Provider
from gateseal.diagnostics import Annotation, GatesealError, Result, redact
from gateseal.paths import resolved
from gateseal.worktree import (
    Worktree,
    locate_worktree,
    operation_lock,
    preparation_directory,
)

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


class InvalidInvocation(GatesealError):
    def __init__(self, command: str | None, message: str) -> None:
        program = " ".join(filter(None, ("gateseal", command)))
        super().__init__(
            "invalid_invocation", f"{message}. Run {program} --help for examples."
        )
        self.command = command


class Parser(argparse.ArgumentParser):
    def __init__(self, *args: Any, command: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.command = command
        """The command this parser reads, such as run or policy lock, or None."""

    def error(self, message: str) -> NoReturn:
        raise InvalidInvocation(self.command, message)


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
        "gateseal check --json",
    )
    _options(parser, True)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    examples = {
        "configure": (
            "gateseal configure --capability hygiene --capability secrets; "
            "gateseal configure --capability python --ci github"
        ),
        "check": "gateseal check --json",
        "upgrade": (
            "gateseal upgrade --policy-tag v2026.12; gateseal upgrade "
            "betterleaks=1.8.0 --reason 'Upstream issue 123' --review-by "
            "2026-12-31; gateseal upgrade betterleaks; gateseal upgrade, run "
            "by a newer gateseal"
        ),
        "run": "gateseal run; gateseal --repo '/work/team repo' run --json",
        "fetch": "gateseal fetch; gateseal fetch --json",
        "gate": "gateseal gate; gateseal gate --base BASE_SHA --head HEAD_SHA; "
        "gateseal gate --merge MERGE_SHA; gateseal gate --after AFTER_SHA; "
        "gateseal gate --before BEFORE_SHA --after AFTER_SHA --json",
        "setup": "gateseal setup; gateseal setup --refresh; gateseal setup --json",
        "status": "gateseal status --json; gateseal status --verbose",
    }
    for command, example in examples.items():
        child = commands.add_parser(
            command, command=command, epilog="Examples: " + example
        )
        _options(child)
        if command == "configure":
            child.add_argument("--capability", action="append")
            child.add_argument(
                "--policy", help="bundled, or a Git policy repository URL"
            )
            child.add_argument("--policy-commit", help="Full commit ID of a Git policy")
            child.add_argument("--policy-path", help="Policy directory, default .")
            child.add_argument("--policy-tag", help="Tag that must name the commit")
            child.add_argument(
                "--ci", choices=sorted(PROVIDERS), help="Write the CI provider's file"
            )
        elif command == "setup":
            child.add_argument(
                "--refresh",
                action="store_true",
                help=(
                    "Also install the commit launcher in the existing clone at "
                    "--repo, and the push launcher if a selected hook runs at a "
                    "push. Remove a push launcher that setup wrote when no "
                    "worktree needs it"
                ),
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
            child.add_argument("--reason", help="Why a Version Exception is needed")
            child.add_argument(
                "--review-by",
                type=date.fromisoformat,
                help="Date to review a Version Exception, such as 2026-12-31",
            )
    policy = commands.add_parser(
        "policy",
        command="policy",
        help="Start a team policy, review and pin tool versions, lock and check it",
        epilog="Examples: gateseal policy init team-policy/policy; "
        "gateseal policy add betterleaks 1.11.0; gateseal policy pin betterleaks "
        "1.11.0; gateseal policy lock; gateseal policy check --json",
    )
    _policy_actions(policy)
    return parser


def _policy_actions(policy: argparse.ArgumentParser) -> None:
    actions = policy.add_subparsers(dest="action", required=True)
    for action in (*POLICY, *REVIEW):
        child = actions.add_parser(action, command=f"policy {action}")
        _options(child, repo=False)
        if action in REVIEW:
            child.add_argument("tool", help="Component, such as betterleaks")
            child.add_argument("version", help="Exact version, such as 1.11.0")
        if action == "init":
            child.add_argument("directory", help="New or empty policy directory")
        else:
            child.add_argument(
                "directory", nargs="?", default=".", help="Policy directory, default ."
            )


GATE_SCOPES = {
    "--base": "Base commit of a pull request or merge group",
    "--head": "Head commit of a pull request or merge group",
    "--merge": "Merge commit of a pull request, whose first parent is the base",
    "--before": "Commit a push replaced, when the event names it",
    "--after": "Commit a push left its ref at",
}
"""gate's revision flags, in groups: --base and --head, or --merge, or --after
with the --before that the event names."""


def _name(arguments: argparse.Namespace) -> str | None:
    """Name the command that the parser chose, such as policy lock, if any."""
    command = cast(str | None, arguments.command)
    action = cast(str | None, getattr(arguments, "action", None))
    return f"{command} {action}" if action else command


def _operation(arguments: argparse.Namespace, in_ci: bool) -> Result:
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
    return _locked(command, worktree, arguments, in_ci)


def _configure(worktree: Worktree, arguments: argparse.Namespace) -> Result:
    with operation_lock(preparation_directory(worktree)):
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


def _locked(
    command: str, worktree: Worktree, arguments: argparse.Namespace, in_ci: bool
) -> Result:
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
    if command == "fetch":
        return fetch.run(locked)
    return checks.gate(worktree, locked, _scope(worktree, arguments), in_ci)


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
    return upgrade.run(
        worktree,
        upgrade.Request(
            None if policy == lock.PolicyRequest() else policy,
            upgrade.components(cast(list[str], arguments.components)),
            arguments.reason,
            arguments.review_by,
        ),
    )


def _scope(worktree: Worktree, arguments: argparse.Namespace) -> ci.Scope | None:
    """Plan the gate's scope from at most one complete group of revision flags."""
    given = cast(dict[str, str | None], vars(arguments))
    base, head, merge = given["base"], given["head"], given["merge"]
    before, after = given["before"], given["after"]
    groups = [(base, head), (merge,), (before, after)]
    named = [group for group in groups if any(flag is not None for flag in group)]
    halved = (base is None) != (head is None) or (before is not None and after is None)
    if len(named) > 1 or halved:
        raise GatesealError(
            "invalid_invocation",
            "Pass --base with --head, or --merge, or --after with the --before "
            "that the event names, or none of them to check every file.",
        )
    # pytestmut: off[bool.and_or] -- the check above sets both or neither
    if base is not None and head is not None:
        return ci.change_scope(worktree, base, head)
    # pytestmut: on
    if merge is not None:
        return ci.merge_scope(worktree, merge)
    if after is not None:
        return ci.push_scope(worktree, before, after)
    return ci.full_scope()


def _provider(environment: Mapping[str, str]) -> Provider | None:
    """Return the CI provider that runs this process, if one does."""
    for provider in PROVIDERS.values():
        variable, value = provider.running
        if environment.get(variable) == value:
            return provider
    return None


def _output(
    result: Result, as_json: bool, verbose: bool, provider: Provider | None
) -> str:
    """Return what main prints: in CI, the full output with the provider's
    commands, and for a failed text result the line that publishes its summary.

    A summary that cannot be written leaves the result's exit code as it is.
    """
    if provider is None:
        return result.render(as_json, verbose)
    shown = result.render(as_json, True, ci=provider)
    if as_json or not result.exit_code:
        return shown
    try:
        published = provider.publish_summary(result.summary(), os.environ)
    except OSError as exc:
        message = f"gateseal could not write the run summary: {redact(str(exc))}"
        published = provider.annotation(Annotation("warning", message))
    return "\n".join(filter(None, (shown, published)))


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    command: str | None = None
    as_json = "--json" in raw
    # pytestmut: off[cmp.membership] -- a Result of a parse error has no details
    full = "--verbose" in raw
    # pytestmut: on
    provider = _provider(os.environ)
    try:
        try:
            arguments, unknown = _parser().parse_known_args(raw)
            if unknown:
                raise InvalidInvocation(
                    _name(arguments), "unrecognized arguments: " + " ".join(unknown)
                )
            as_json, full = bool(arguments.json), bool(arguments.verbose)
            command = _name(arguments) or "status"
            result = _operation(arguments, provider is not None)
        except OSError as exc:
            raise GatesealError("filesystem_error", str(exc), 4) from exc
        except KeyboardInterrupt:
            raise GatesealError(
                "interrupted", "Operation interrupted. Retry the same command.", 4
            ) from None
    except GatesealError as exc:
        result = Result(
            exc.command if isinstance(exc, InvalidInvocation) else command,
            exc.code,
            str(exc),
            exc.exit_code,
            next_steps=(exc.next_step,) if exc.next_step else (),
            details=exc.details,
        )
    print(_output(result, as_json, full, provider))
    return result.exit_code
