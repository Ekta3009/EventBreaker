"""Report shows what each scenario did and how its counts differ from the fault-free baseline."""
from eventbreaker.models.observation import ObservationEntry as E
from eventbreaker.report.reporter import _call_status, describe_test
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig


def sc(pattern, faults=()):
    return ChaosScenario(scenarioType="X", reason="r", expectedConcern="c",
                         testConfig=TestConfig(callPattern=pattern, faultInjections=list(faults)))


def test_describe_fault_scenario():
    text = describe_test(sc("SINGLE", [FaultInjection(dep="handler", method="handle", fault="THROW")]))
    assert text == "Delivered the event once and made `handler.handle` throw on every call"


def test_describe_duplicate():
    assert describe_test(sc("DUPLICATE")).startswith("Delivered the same event twice in a row")


def test_status_threw_is_visible_even_at_one_call():
    assert _call_status(E(target="h.handle", callCount=1, threw=True), 1, None) == "**threw**"


def test_status_against_baseline():
    assert _call_status(E(target="p.insert", callCount=1), 1, 3) == "**2 missing**"
    assert _call_status(E(target="p.insert", callCount=6), 6, 3) == "**3 extra**"
    assert _call_status(E(target="p.insert", callCount=3), 3, 3) == "✓"
    assert _call_status(None, 0, 1) == "**1 missing**"


def test_save_keeps_history_and_latest(tmp_path):
    from eventbreaker.models.consumer import ConsumerAnalysis
    from eventbreaker.report.reporter import save
    a = ConsumerAnalysis(className="KafkaRemoteMessageListener", methodName="onMessage", eventType="E")
    first, second = save("one", a, tmp_path), save("two", a, tmp_path)
    folder = tmp_path / "KafkaRemoteMessageListener.onMessage"
    assert first == second == folder / "latest.md"
    assert second.read_text() == "two"                       # latest = newest run
    dated = [p for p in folder.iterdir() if p.name != "latest.md"]
    assert sorted(p.read_text() for p in dated) == ["one", "two"]   # history kept, never overwritten


def test_reports_go_to_the_project_root(tmp_path, monkeypatch):
    from eventbreaker.report.reporter import reports_dir
    # Multi-module Maven project: root pom + module pom; the listener sits in the module.
    (tmp_path / "pom.xml").write_text("<project/>")
    module = tmp_path / "billing"
    src = module / "src" / "main" / "java" / "x"
    src.mkdir(parents=True)
    (module / "pom.xml").write_text("<project/>")
    listener = src / "L.java"
    listener.write_text("class L {}")
    elsewhere = tmp_path.parent
    monkeypatch.chdir(elsewhere)
    assert reports_dir(listener) == tmp_path / "eventbreaker-reports"
    # A loose copy outside any project: the current directory.
    loose = tmp_path / "loose.java"
    loose.write_text("class L {}")
    assert reports_dir(loose) == elsewhere / "eventbreaker-reports"


def test_same_class_method_in_two_modules_gets_separate_folders(tmp_path):
    from eventbreaker.models.consumer import ConsumerAnalysis
    from eventbreaker.report.reporter import save
    a = ConsumerAnalysis(className="SmsNotificationListener", methodName="process", eventType="E")
    sms, mail = tmp_path / "sms" / "L.java", tmp_path / "mail" / "L.java"
    first = save("sms", a, tmp_path / "out", source=sms)
    second = save("mail", a, tmp_path / "out", source=mail)
    again = save("sms 2", a, tmp_path / "out", source=sms)
    assert first == again and first.read_text() == "sms 2"   # same file → same folder
    assert second != first and second.read_text() == "mail"  # other file → its own folder
    assert second.parent.name == "SmsNotificationListener.process@mail"
