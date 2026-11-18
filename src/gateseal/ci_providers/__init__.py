"""The CI providers whose gate file this compiler writes.

The Manifest's provider names, the --ci choices and the files the Lock can own
all come from PROVIDERS. A writer imports model, never this package.
"""

from collections.abc import Callable
from dataclasses import dataclass

from gateseal.ci_providers import azure, github
from gateseal.ci_providers.model import GateJob


@dataclass(frozen=True)
class Provider:
    """One CI provider.

    path is the file it owns, and write renders that file. running is the
    environment variable and value that every run on the provider sets.
    warning turns a message into the command that the provider shows as an
    annotation of the run.
    """

    path: str
    write: Callable[[GateJob], bytes]
    running: tuple[str, str]
    warning: Callable[[str], str]


PROVIDERS = {
    "azure": Provider(azure.PIPELINE, azure.pipeline, azure.RUNNING, azure.warning),
    "github": Provider(
        github.WORKFLOW, github.workflow, github.RUNNING, github.warning
    ),
}
