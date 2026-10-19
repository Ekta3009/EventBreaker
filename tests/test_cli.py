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


def test_detached_delivery_from_async_or_after_commit(tmp_path):
    from eventbreaker.cli.main import _detached_delivery
    from eventbreaker.models.consumer import ConsumerAnalysis
    f = tmp_path / "L.java"
    f.write_text(
        "class L {\n"
        "  @TransactionalEventListener\n  public void a(E e) {}\n"
        "  @TransactionalEventListener(phase = TransactionPhase.BEFORE_COMMIT)\n  public void b(E e) {}\n"
        "  @Async\n  @EventListener\n  public void c(E e) {}\n"
        "  @EventListener\n  public void d(E e) {}\n"
        "}\n")
    def det(m, ann):
        return _detached_delivery(f, ConsumerAnalysis(className="L", methodName=m, eventType="E", listenerAnnotation=ann))
    assert det("a", "TransactionalEventListener") and det("c", "EventListener")
    assert not det("b", "TransactionalEventListener") and not det("d", "EventListener")


def test_kafka_error_handling_found_in_the_repository_or_own_library(tmp_path):
    import zipfile as zf
    from eventbreaker.cli.main import _kafka_error_handling
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    src = repo / "svc/src/main/java/org/acme/pay"
    src.mkdir(parents=True)
    listener = src / "C.java"
    listener.write_text("package org.acme.pay;\nclass C {\n  @KafkaListener(topics = \"t\")\n  public void on(String m) {}\n}\n")
    assert _kafka_error_handling(listener, "on", []) == "default"
    lib = tmp_path / ".m2/repository/org/acme/platform/tracer/1.0/tracer-1.0.jar"
    lib.parent.mkdir(parents=True)
    with zf.ZipFile(lib, "w") as z:
        z.writestr("org/acme/tracer/Handler.class", b"...org/springframework/kafka/listener/DefaultErrorHandler...")
    other = tmp_path / ".m2/repository/com/other/x/1.0/x-1.0.jar"
    other.parent.mkdir(parents=True)
    with zf.ZipFile(other, "w") as z:
        z.writestr("com/other/H.class", b"DefaultErrorHandler")
    assert _kafka_error_handling(listener, "on", [other]) == "default"     # someone else's library
    assert _kafka_error_handling(listener, "on", [lib]) == "configured"    # the organisation's own platform library
    (repo / "svc/src/main/java/org/acme/pay/KafkaConfig.java").write_text(
        "class KafkaConfig { DeadLetterPublishingRecoverer r; /* kafka */ }")
    assert _kafka_error_handling(listener, "on", []) == "configured"
