import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    token = os.environ.get("GH_TOKEN")
    if token is None:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        token = result.stdout.strip()
    if not token or any(c.isspace() for c in token):
        raise SystemExit("A repository-scoped GitHub token is required.")
    descriptor, name = tempfile.mkstemp(prefix="gateseal-netrc-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                "machine raw.githubusercontent.com\nlogin __token__\npassword "
                + token
                + "\n"
            )
        # The Git policy test hands the token to Git only through a credential
        # helper scoped to the policy repository's URL.
        env = dict(os.environ, NETRC=name, GATESEAL_POLICY_TOKEN=token)
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        arguments = sys.argv[1:] or ["-m", "network"]
        return subprocess.run(
            [sys.executable, "-m", "pytest", *arguments], env=env, check=False
        ).returncode
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
