"""Git starts no background maintenance in the suite's repositories.

After a commit, Git starts git maintenance run --auto by default, and that
process can hold .git/objects/maintenance.lock after the commit returns. A
fixture that copies the repository then can list the lock and find it gone.
"""

import json
import os

from installation import clone, success


def started(trace):
    """Return the arguments of each process that Git's trace says it started."""
    events = map(json.loads, trace.read_text().splitlines())
    return [event["argv"] for event in events if event["event"] == "child_start"]


def test_a_commit_starts_no_background_maintenance(tmp_path):
    root = clone(tmp_path / "origin", tmp_path / "consumer")
    trace = tmp_path / "trace.json"
    env = dict(os.environ, GIT_TRACE2_EVENT=str(trace))
    success(["git", "commit", "--allow-empty", "-m", "Empty"], root, env)
    assert [argv for argv in started(trace) if "maintenance" in argv] == []
