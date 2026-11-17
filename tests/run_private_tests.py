import os
import subprocess
import sys


def main() -> int:
    token = os.environ.get("GH_TOKEN")
    if token is None:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        token = result.stdout.strip()
    if not token or any(c.isspace() for c in token):
        raise SystemExit("A repository-scoped GitHub token is required.")
    # The Git policy test hands the token to Git only through a credential
    # helper scoped to the policy repository's URL.
    env = dict(os.environ, GATESEAL_POLICY_TOKEN=token)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    arguments = sys.argv[1:] or ["-m", "network"]
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments], env=env, check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
