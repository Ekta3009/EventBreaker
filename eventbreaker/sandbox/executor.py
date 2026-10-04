import os
from pathlib import Path

from contree_sdk import ContreeSync
from contree_sdk.sdk.exceptions import FailedOperationError, OperationTimedOutError

# Alpine 3.20 is the lightest available image that supports openjdk21.
_IMAGE = "alpine:3.20"

# Destination path inside the sandbox container.
_JAR_PATH = "/app/eventbreaker.jar"

# Total sandbox timeout: ~30s to install JRE + a few seconds to run the JAR.
_TIMEOUT_SECONDS = 180


class SandboxExecutor:
    """Runs the pre-built fat JAR inside a Nebius Sandbox container.

    Requires NEBIUS_API_KEY and NEBIUS_PROJECT_ID in the environment.
    """

    def execute(self, jar_path: Path) -> tuple[str, str]:
        """Upload jar_path to sandbox, install JRE, run it.

        Returns:
            (stdout, stderr) as strings.

        Raises:
            EnvironmentError: if required env vars are missing.
            RuntimeError: if the sandbox operation fails or times out.
        """
        if not os.environ.get("NEBIUS_PROJECT_ID"):
            raise EnvironmentError(
                "NEBIUS_PROJECT_ID environment variable is not set. "
                "Add it to your .env file."
            )

        client = ContreeSync()
        sandbox = client.images.use(_IMAGE)

        try:
            result = sandbox.run(
                shell=(
                    "apk add --no-cache openjdk21-jre-headless >/dev/null 2>&1 "
                    f"&& java -jar {_JAR_PATH}"
                ),
                files={_JAR_PATH: jar_path},
                timeout=_TIMEOUT_SECONDS,
            ).wait()
        except OperationTimedOutError as e:
            raise RuntimeError(
                f"Sandbox execution timed out after {_TIMEOUT_SECONDS}s: {e}"
            ) from e
        except FailedOperationError as e:
            raise RuntimeError(f"Sandbox operation failed: {e}") from e

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return stdout, stderr
