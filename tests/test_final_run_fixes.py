"""Gaps found in the final sandbox run (INVENTORY "Final run"). Synthetic inputs only."""
import subprocess
import zipfile
from pathlib import Path

from eventbreaker.classpath.resolver import _pom_without_dependency_filters
from eventbreaker.scenarios.executor import _class_entries, _jar_services, _sandbox_jar, _unversioned


# ── Sandbox jar: multi-release classes, same-package references, service providers ──

def test_multi_release_class_is_taken_from_its_version_folder():
    names = ["a/Util.class", "META-INF/versions/9/a/Locator.class", "META-INF/versions/9/a/Util.class",
             "META-INF/versions/25/a/Util.class", "module-info.class"]
    assert _class_entries(names) == {
        "a/Util.class": "META-INF/versions/9/a/Util.class",      # newest version the sandbox (21+) can run
        "a/Locator.class": "META-INF/versions/9/a/Locator.class",  # exists only in the version folder
    }


def test_jdeps_version_prefix_is_dropped():
    assert _unversioned("9/org.apache.logging.log4j.util.StackLocator") == "org.apache.logging.log4j.util.StackLocator"
    assert _unversioned("org.acme.Foo") == "org.acme.Foo"


def _jar(path: Path, sources: dict[str, str], files: dict[str, str], tmp: Path, cp: list[Path] = ()) -> Path:
    src = tmp / f"src-{path.stem}"
    out = tmp / f"cls-{path.stem}"
    for name, code in sources.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(code)
    subprocess.run(["javac", "-d", str(out), "-cp", ":".join(map(str, cp)) or ".",
                    *map(str, src.rglob("*.java"))], check=True)
    with zipfile.ZipFile(path, "w") as z:
        for f in out.rglob("*.class"):
            z.write(f, f.relative_to(out).as_posix())
        for name, text in files.items():
            z.writestr(name, text)
    return path


def test_sandbox_jar_keeps_service_providers_resources_and_same_package_classes(tmp_path):
    api = _jar(tmp_path / "api.jar", {
        "api/Provider.java": "package api; public interface Provider { String name(); }",
        "api/Manager.java": "package api; public class Manager { public static String name() {"
                            " return Helper.first(java.util.ServiceLoader.load(Provider.class)); } }",
        "api/Helper.java": "package api; class Helper { static String first(Iterable<Provider> p) {"
                           " for (Provider x : p) return x.name(); return \"none\"; } }",
    }, {"META-INF/services/api.Provider": "api.Unrelated\n"}, tmp_path)
    other = _jar(tmp_path / "other.jar", {"other/Lonely.java": "package other; public class Lonely {}"},
                 {"other/big.dat": "y"}, tmp_path)
    impl = _jar(tmp_path / "impl.jar", {
        "impl/Impl.java": "package impl; public class Impl implements api.Provider { public String name() {"
                          " return Names.NAME; } }",
        "impl/Names.java": "package impl; class Names { static final String NAME = new String(\"impl\");"
                           " static Object pick(String s) throws Exception {"
                           " return Class.forName(\"impl.\" + s); } }",   # sibling chosen by computed name
        "impl/Unused.java": "package impl; public class Unused {}",
        "impl/HalfIncluded.java": "package impl; public class HalfIncluded { other.Lonely l = new other.Lonely(); }",
    }, {"META-INF/services/api.Provider": "impl.Impl # the provider\n", "impl/config.dat": "x"},
        tmp_path, [api, other])
    fat = _jar(tmp_path / "eventbreaker-execution.jar", {
        "app/Main.java": "package app; public class Main { public static void main(String[] a) {"
                         " System.out.println(api.Manager.name()); } }",
    }, {}, tmp_path, [api])

    with zipfile.ZipFile(_sandbox_jar(fat, [api, impl, other])) as z:
        names = set(z.namelist())
        services = z.read("META-INF/services/api.Provider").decode()
    assert {"api/Helper.class", "impl/Impl.class", "impl/Names.class", "impl/config.dat"} <= names
    assert "impl/Unused.class" in names                     # package of a used class that loads by name
    assert "impl/HalfIncluded.class" not in names           # ...but not a sibling whose references are missing
    assert {"other/Lonely.class", "other/big.dat"}.isdisjoint(names)   # unused jar stays out
    assert "impl.Impl" in services and "api.Unrelated" in services       # merged, not overwritten


def test_jar_services_reads_implementations_without_comments(tmp_path):
    jar = tmp_path / "s.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/services/a.Spi", "# header\nb.First\n\nb.Second # note\n")
    assert _jar_services(jar) == {"a.Spi": ["b.First", "b.Second"]}


# ── Maven classpath: the module's own dependency-plugin filters ──

POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent><groupId>g</groupId><artifactId>parent</artifactId><version>1</version></parent>
  <artifactId>starter</artifactId>
  <build><plugins><plugin>
    <groupId>org.apache.maven.plugins</groupId><artifactId>maven-dependency-plugin</artifactId>
    <configuration>
      <outputDirectory>x</outputDirectory>
      <includeGroupIds>org.webjars</includeGroupIds><includeArtifactIds>swagger-ui</includeArtifactIds>
      <scope>provided</scope>
    </configuration>
  </plugin></plugins></build>
</project>"""


def test_pom_copy_drops_dependency_filters_and_keeps_the_parent(tmp_path):
    module = tmp_path / "project" / "starter"
    module.mkdir(parents=True)
    (module.parent / "pom.xml").write_text("<project/>")
    (module / "pom.xml").write_text(POM)
    work = tmp_path / "work"
    work.mkdir()

    copy = _pom_without_dependency_filters(module / "pom.xml", work).read_text()
    assert "includeGroupIds" not in copy and "includeArtifactIds" not in copy and "<scope>" not in copy
    assert "<outputDirectory>x</outputDirectory>" in copy                 # other configuration kept
    rel = copy.split("<relativePath>")[1].split("</relativePath>")[0]
    assert (work / rel).resolve() == (module.parent / "pom.xml").resolve()
    assert (module / "pom.xml").read_text() == POM                        # project untouched


def test_pom_without_filters_needs_no_copy(tmp_path):
    (tmp_path / "pom.xml").write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"/>')
    assert _pom_without_dependency_filters(tmp_path / "pom.xml", tmp_path) is None
