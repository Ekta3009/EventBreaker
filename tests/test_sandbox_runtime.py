"""Sandbox runtime choice: the oldest image whose JRE can load every class in the uploaded JAR."""
import zipfile

import pytest

from eventbreaker.sandbox.executor import required_java, runtime_for


def _class(java: int) -> bytes:
    return b"\xca\xfe\xba\xbe" + (0).to_bytes(2, "big") + (java + 44).to_bytes(2, "big") + b"\x00" * 8


def _jar(tmp_path, **classes) -> object:
    jar = tmp_path / "x.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        for name, java in classes.items():
            z.writestr(name.replace("__", "/") + ".class", _class(java))
    return jar


def test_java_21_and_older_keeps_the_original_image(tmp_path):
    jar = _jar(tmp_path, a__A=17, b__B=21)
    assert required_java(jar) == 21
    assert runtime_for(jar) == ("alpine:3.20", "openjdk21-jre-headless")


def test_java_25_classes_get_the_openjdk25_image(tmp_path):
    jar = _jar(tmp_path, a__A=21, org__apache__syncope__S=25)
    assert runtime_for(jar) == ("alpine:latest", "openjdk25-jre-headless")


def test_multi_release_entries_do_not_raise_the_version(tmp_path):
    jar = _jar(tmp_path, a__A=17, META_INF__versions__25__a__A=25)
    jar2 = tmp_path / "y.jar"
    with zipfile.ZipFile(jar) as src, zipfile.ZipFile(jar2, "w") as dst:
        for n in src.namelist():
            dst.writestr(n.replace("META_INF", "META-INF"), src.read(n))
    assert required_java(jar2) == 17


def test_newer_than_any_sandbox_runtime_is_a_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="compiled for Java 26"):
        runtime_for(_jar(tmp_path, a__A=26))
