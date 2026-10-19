"""CLI helpers that read delivery and transaction facts about a listener from its build and source."""
import zipfile

from eventbreaker.cli.main import _PERSISTENT_BUS, _method_annotated, _on_classpath, _transactional_rollback


def test_persistent_bus_found_in_a_jar_or_class_directory(tmp_path):
    jar = tmp_path / "killbill-queue.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr(_PERSISTENT_BUS, b"")
    plain = tmp_path / "guava.jar"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("com/google/common/eventbus/EventBus.class", b"")
    classes = tmp_path / "classes"
    (classes / "org/killbill/bus/api").mkdir(parents=True)
    (classes / _PERSISTENT_BUS).write_bytes(b"")
    assert _on_classpath([plain, jar], _PERSISTENT_BUS)
    assert _on_classpath([classes], _PERSISTENT_BUS)
    assert not _on_classpath([plain], _PERSISTENT_BUS)


def test_allow_concurrent_events_is_read_from_the_methods_own_annotations(tmp_path):
    f = tmp_path / "S.java"
    f.write_text(
        "class S {\n"
        "    @Subscribe\n    @AllowConcurrentEvents\n    public void a(E e) { b(e); }\n"
        "    // @AllowConcurrentEvents\n    @Subscribe\n    public void b(E e) {}\n"
        "}\n")
    assert _method_annotated(f, "a", "AllowConcurrentEvents")
    assert not _method_annotated(f, "b", "AllowConcurrentEvents")   # comment and other method ignored


def test_transactional_rollback_from_method_then_class(tmp_path):
    f = tmp_path / "L.java"
    f.write_text(
        "@Service\n@Transactional(readOnly = true)\npublic class L {\n"
        "    @EventListener\n    @Transactional(rollbackFor = {IOException.class})\n    public void a(E e) {}\n"
        "    @TransactionalEventListener\n    public void b(E e) {}\n"
        "    @Transactional(propagation = Propagation.NOT_SUPPORTED)\n    public void c(E e) {}\n"
        "}\n")
    assert _transactional_rollback(f, "a", "x.L") == "any"
    assert _transactional_rollback(f, "b", "x.L") == "unchecked"   # class-level; not @TransactionalEventListener
    assert _transactional_rollback(f, "c", "x.L") == ""


def test_no_transactional_annotation_means_no_rollback(tmp_path):
    f = tmp_path / "M.java"
    f.write_text("// @Transactional\n@Component\nclass M {\n    @TransactionalEventListener\n    void a(E e) {}\n}\n")
    assert _transactional_rollback(f, "a", "M") == ""
