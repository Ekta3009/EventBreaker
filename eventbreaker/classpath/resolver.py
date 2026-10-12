"""Auto classpath: ask the listener's own build (Maven or Gradle) for its dependency jars.

A listener in `<module>/src/main/java/...` is compiled and run against the module's
real classpath, so a developer can run EventBreaker in their checkout with no flags.
Results are cached per module and refreshed when any build file changes.
"""
import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_CACHE_DIR = Path.home() / ".eventbreaker" / "cache" / "classpath"
_INIT_SCRIPT = Path(__file__).with_name("print-classpath.init.gradle")
_MARKER = "EB_CLASSPATH="
_MAVEN_FILES = ("pom.xml",)
_GRADLE_FILES = ("build.gradle", "build.gradle.kts")
_GRADLE_ROOT_FILES = ("settings.gradle", "settings.gradle.kts")
# Files whose change can change a module's classpath.
_BUILD_FILES = _MAVEN_FILES + _GRADLE_FILES + _GRADLE_ROOT_FILES + (
    "gradle.properties", "gradle/libs.versions.toml",
)


class ClasspathError(Exception):
    """The module's build could not produce a classpath; the message says what to run."""


@dataclass(frozen=True)
class Module:
    dir: Path           # directory holding the module's build file
    tool: str           # "maven" | "gradle"
    root: Path          # Gradle: settings dir (where the wrapper lives); Maven: same as dir


def find_module(listener: Path) -> Module | None:
    """The build module a production listener belongs to, or None.

    Only files under `<module>/src/main/java` qualify: test resources and loose
    copies have no build of their own (use --classpath for those).
    """
    path = listener.resolve()
    parts = path.parts
    for i in range(len(parts) - 3, 0, -1):
        if parts[i:i + 3] == ("src", "main", "java"):
            module_dir = Path(*parts[:i])
            break
    else:
        return None
    if any((module_dir / f).exists() for f in _MAVEN_FILES):
        return Module(module_dir, "maven", module_dir)
    if any((module_dir / f).exists() for f in _GRADLE_FILES):
        return Module(module_dir, "gradle", _gradle_root(module_dir))
    return None


def resolve(module: Module) -> list[Path]:
    """The module's compiled classes + dependency jars (cached until a build file changes)."""
    cache_file = _CACHE_DIR / f"{_cache_key(module)}.txt"
    if cache_file.exists():
        entries = _split(cache_file.read_text())
        if entries and all(e.exists() for e in entries):
            return entries
    entries = _maven(module) if module.tool == "maven" else _gradle(module)
    entries = [e for e in entries if e.exists()]
    if not entries:
        raise ClasspathError(f"{module.tool} returned an empty classpath for {module.dir}")
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(os.pathsep.join(str(e) for e in entries))
    return entries


def _maven(module: Module) -> list[Path]:
    classes = module.dir / "target" / "classes"
    if not classes.is_dir():
        raise ClasspathError(
            f"{module.dir.name} is not compiled yet — run `mvn compile` in {module.dir} first."
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "cp.txt"
        _run(
            [_wrapper(module.dir, "mvnw") or "mvn", "-q", "--batch-mode", "-f", str(module.dir / "pom.xml"),
             # Runtime scope (compile + runtime), like Gradle's runtimeClasspath: test-scope
             # Mockito/JUnit would clash with the harness's own.
             "dependency:build-classpath", "-DincludeScope=runtime", f"-Dmdep.outputFile={out}"],
            module.dir,
            hint="If it depends on sibling modules, run `mvn install -DskipTests` at the project root first.",
        )
        jars = _split(out.read_text()) if out.exists() else []
    return [classes] + jars


def _gradle(module: Module) -> list[Path]:
    gradle = _wrapper(module.root, "gradlew") or "gradle"
    rel = module.dir.relative_to(module.root).parts
    task = ":" + ":".join(rel + ("ebClasspath",)) if rel else ":ebClasspath"
    output = _run(
        [gradle, "-q", "--console=plain", "-I", str(_INIT_SCRIPT), task],
        module.root,
        hint=f"Check that `{Path(gradle).name} :{':'.join(rel)}:classes` builds in {module.root}.",
    )
    for line in reversed(output.splitlines()):
        if line.startswith(_MARKER):
            return _split(line[len(_MARKER):])
    raise ClasspathError(f"Gradle printed no classpath for {module.dir} (task {task}).")


def _run(cmd: list[str], cwd: Path, hint: str) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        raise ClasspathError(f"`{cmd[0]}` is not installed or not on PATH (needed to read {cwd}'s build).")
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
        raise ClasspathError(f"`{' '.join([Path(cmd[0]).name, *cmd[1:]])}` failed in {cwd}:\n{tail}\n{hint}")
    return proc.stdout


def _gradle_root(module_dir: Path) -> Path:
    """Nearest ancestor with a settings file (the multi-project root), else the module itself."""
    for d in (module_dir, *module_dir.parents):
        if any((d / f).exists() for f in _GRADLE_ROOT_FILES):
            return d
    return module_dir


def _wrapper(start: Path, name: str) -> str | None:
    """The project's own build wrapper (./gradlew, ./mvnw) at or above start."""
    for d in (start, *start.parents):
        if (d / name).is_file():
            return str(d / name)
    return None


def _cache_key(module: Module) -> str:
    """Module path + mtimes of every build file from the module up to the filesystem root."""
    stamps = [str(module.dir)]
    for d in (module.dir, *module.dir.parents):
        for f in _BUILD_FILES:
            if (d / f).exists():
                stamps.append(f"{d / f}:{(d / f).stat().st_mtime_ns}")
    return hashlib.sha256("\0".join(stamps).encode()).hexdigest()[:16]


def _split(text: str) -> list[Path]:
    return [Path(p) for p in text.strip().split(os.pathsep) if p.strip()]
