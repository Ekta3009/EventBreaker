import os
import zipfile
from pathlib import Path

from contree_sdk import ContreeSync
from contree_sdk.config import ContreeConfig
from contree_sdk.sdk.exceptions import FailedOperationError, OperationTimedOutError
from contree_sdk.sdk.exceptions.base import ContreeError

# (highest Java version, image, JRE package), oldest first: the first one that can load every class
# in the JAR is used. Alpine 3.20 is the lightest image with openjdk21. The sandbox catalog has no pinned
# Alpine >= 3.23 (the first with openjdk25), so Java 22-25 uses alpine:latest (3.23.3 on 2026-10-19); the
# package name still pins the Java version.
_RUNTIMES = (
    (21, "alpine:3.20", "openjdk21-jre-headless"),
    (25, "alpine:latest", "openjdk25-jre-headless"),
)

# Destination path inside the sandbox container.
_JAR_PATH = "/app/eventbreaker.jar"

# Total sandbox timeout: ~30s to install JRE + a few seconds to run the JAR.
_TIMEOUT_SECONDS = 180

# Per-HTTP-request timeout. The SDK default (10s) is too short to upload
# an execution JAR (~9 MB) on an ordinary home connection.
_TRANSPORT_TIMEOUT_SECONDS = 120.0


class SandboxExecutor:
    """Runs the pre-built fat JAR inside a Nebius Sandbox container.

    Requires NEBIUS_API_KEY and NEBIUS_PROJECT_ID in the environment.
    """

    @staticmethod
    def _run_once(sandbox, jar_path: Path, package: str):
        return sandbox.run(
            shell=(
                f"apk add --no-cache {package} >/dev/null 2>&1 "
                f"&& java -jar {_JAR_PATH}"
            ),
            files={_JAR_PATH: jar_path},
            timeout=_TIMEOUT_SECONDS,
        ).wait()

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

        image, package = runtime_for(jar_path)
        client = ContreeSync(ContreeConfig(transport_timeout=_TRANSPORT_TIMEOUT_SECONDS))
        sandbox = client.images.use(image)

        try:
            try:
                result = self._run_once(sandbox, jar_path, package)
            except (OperationTimedOutError, FailedOperationError):
                raise
            except ContreeError:
                # A dropped connection (e.g. during the JAR upload) is transient: retry once.
                result = self._run_once(sandbox, jar_path, package)
        except OperationTimedOutError as e:
            raise RuntimeError(
                f"Sandbox execution timed out after {_TIMEOUT_SECONDS}s: {e}"
            ) from e
        except FailedOperationError as e:
            raise RuntimeError(f"Sandbox operation failed: {e}") from e
        except ContreeError as e:  # upload / transport / API errors, twice
            raise RuntimeError(f"Sandbox request failed: {type(e).__name__}: {e}") from e

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return stdout, stderr


def required_java(jar_path: Path) -> int:
    """The Java version needed to load every class in the JAR (class-file major version - 44).

    Multi-release entries (META-INF/versions/N/) are skipped: an older JVM never loads them.
    """
    major = 0
    with zipfile.ZipFile(jar_path) as z:
        for name in z.namelist():
            if not name.endswith(".class") or name.startswith("META-INF/versions/"):
                continue
            with z.open(name) as f:
                head = f.read(8)
            if len(head) == 8 and head[:4] == b"\xca\xfe\xba\xbe":
                major = max(major, int.from_bytes(head[6:8], "big"))
    return max(major - 44, 8)


def runtime_for(jar_path: Path) -> tuple[str, str]:
    """(image, JRE package) for the oldest sandbox runtime that can load the JAR."""
    needed = required_java(jar_path)
    for version, image, package in _RUNTIMES:
        if needed <= version:
            return image, package
    raise RuntimeError(
        f"the project is compiled for Java {needed}; the sandbox offers up to Java {_RUNTIMES[-1][0]}. "
        "Run on the local JVM instead (leave NEBIUS_PROJECT_ID empty) with a matching JDK."
    )
