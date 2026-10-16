"""Auto classpath: which module a listener belongs to, how the build is asked, and caching."""
import os
from pathlib import Path

import pytest

from eventbreaker.classpath import resolver
from eventbreaker.classpath.resolver import ClasspathError, Module, find_module, resolve


def listener(module_dir, build_file):
    (module_dir / build_file).parent.mkdir(parents=True, exist_ok=True)
    (module_dir / build_file).write_text("")
    src = module_dir / "src/main/java/com/acme/OrderListener.java"
    src.parent.mkdir(parents=True)
    src.write_text("class OrderListener {}")
    return src


def test_maven_module(tmp_path):
    src = listener(tmp_path / "orders", "pom.xml")
    assert find_module(src) == Module(tmp_path / "orders", "maven", tmp_path / "orders")


def test_gradle_submodule_runs_from_settings_root(tmp_path):
    (tmp_path / "settings.gradle").write_text("")
    src = listener(tmp_path / "services/orders", "build.gradle.kts")
    assert find_module(src) == Module(tmp_path / "services/orders", "gradle", tmp_path)


def test_files_outside_main_sources_have_no_module(tmp_path):
    (tmp_path / "pom.xml").write_text("")
    copy = tmp_path / "src/test/resources/OrderListener.java"
    copy.parent.mkdir(parents=True)
    copy.write_text("")
    assert find_module(copy) is None


def test_gradle_task_path_and_marker_line(tmp_path, monkeypatch):
    jar = tmp_path / "dep.jar"
    jar.write_text("")
    seen = {}

    def fake_run(cmd, cwd, hint):
        seen["cmd"], seen["cwd"] = cmd, cwd
        return f"some warning\nEB_CLASSPATH={tmp_path}{os.pathsep}{jar}\n"

    monkeypatch.setattr(resolver, "_run", fake_run)
    module = Module(tmp_path / "services/orders", "gradle", tmp_path)
    assert resolver._gradle(module) == [tmp_path, jar]
    assert seen["cmd"][-1] == ":services:orders:ebClasspath" and seen["cwd"] == tmp_path


def test_maven_needs_compiled_classes(tmp_path):
    with pytest.raises(ClasspathError, match="mvn compile"):
        resolver._maven(Module(tmp_path, "maven", tmp_path))


def test_cache_reused_until_a_build_file_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(resolver, "_CACHE_DIR", tmp_path / "cache")
    (tmp_path / "pom.xml").write_text("")
    jar = tmp_path / "dep.jar"
    jar.write_text("")
    calls = []
    monkeypatch.setattr(resolver, "_maven", lambda m: calls.append(1) or [jar])
    module = Module(tmp_path, "maven", tmp_path)
    assert resolve(module) == [jar] and resolve(module) == [jar]
    assert len(calls) == 1
    os.utime(tmp_path / "pom.xml", ns=(1, 1))
    resolve(module)
    assert len(calls) == 2


def test_maven_overrides_project_exclude_properties(tmp_path, monkeypatch):
    """A project's own excludeGroupIds (hawkbit sets it for its license tool) must not drop dependencies."""
    (tmp_path / "target/classes").mkdir(parents=True)
    jar = tmp_path / "dep.jar"
    seen = {}

    def fake_run(cmd, cwd, hint):
        seen["cmd"] = cmd
        out = next(a for a in cmd if a.startswith("-Dmdep.outputFile=")).split("=", 1)[1]
        Path(out).write_text(str(jar))

    monkeypatch.setattr(resolver, "_run", fake_run)
    assert resolver._maven(Module(tmp_path, "maven", tmp_path)) == [tmp_path / "target/classes", jar]
    assert "-DexcludeGroupIds=" in seen["cmd"] and "-DincludeScope=runtime" in seen["cmd"]
