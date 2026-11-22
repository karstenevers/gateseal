"""The CI providers whose gate file this compiler writes.

The Manifest's provider names, the --ci choices and the files the Lock can own
all come from PROVIDERS. A writer imports model, never this package.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from gateseal.ci_providers import azure, github
from gateseal.ci_providers.model import GateJob
from gateseal.diagnostics import Annotation


@dataclass(frozen=True)
class Provider:
    """One CI provider.

    path is the file it owns, and write renders that file. running is the
    environment variable and value that every run on the provider sets.
    annotation turns an Annotation into the command that the provider shows on
    the run, or on a line of a file, and quote returns text that the provider
    prints without running a command in it. publish_summary publishes Markdown
    as the run's summary, from the provider's environment variables, and
    returns a line to print, or "". runner_key is the [ci] key that names a
    machine of the team's own, and runner parses its value into GateJob.runner.
    """

    path: str
    write: Callable[[GateJob], bytes]
    running: tuple[str, str]
    annotation: Callable[[Annotation], str]
    quote: Callable[[str], str]
    publish_summary: Callable[[str, Mapping[str, str]], str]
    runner_key: str
    runner: Callable[[object], tuple[str, ...]]


PROVIDERS = {
    "azure": Provider(
        azure.PIPELINE,
        azure.pipeline,
        azure.RUNNING,
        azure.annotation,
        azure.quote,
        azure.publish_summary,
        azure.RUNNER_KEY,
        azure.runner,
    ),
    "github": Provider(
        github.WORKFLOW,
        github.workflow,
        github.RUNNING,
        github.annotation,
        github.quote,
        github.publish_summary,
        github.RUNNER_KEY,
        github.runner,
    ),
}
