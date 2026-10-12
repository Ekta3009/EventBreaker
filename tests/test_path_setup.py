"""Path setup: which calls a scenario targets, whether a run reached them, and the retry feedback."""
from eventbreaker.agent import path_setup
from eventbreaker.agent.path_setup import (
    _parse, forget_plan, not_reached, reach_feedback, reusable_setup, target_calls,
)
from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo
from eventbreaker.models.observation import ObservationEntry as E
from eventbreaker.models.observation import ObservationResult
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, ReturnValue, TestConfig


def sc(pattern="SINGLE", faults=(), dep=None, method=None):
    return ChaosScenario(scenarioType="X", reason="r", expectedConcern="c",
                         targetDependency=dep, targetMethod=method,
                         testConfig=TestConfig(callPattern=pattern, faultInjections=list(faults)))


def test_targets_are_faults_else_declared_target():
    assert target_calls(sc(faults=[FaultInjection(dep="repo", method="save", fault="THROW")])) == ["repo.save"]
    assert target_calls(sc("DUPLICATE", dep="repo", method="save")) == ["repo.save"]
    assert target_calls(sc("DUPLICATE")) == []


def test_not_reached():
    run = ObservationResult(scenario="X", observations=[E(target="a.read", callCount=1), E(target="repo.save", callCount=0)])
    assert not_reached(run, ["repo.save"]) == ["repo.save"]
    assert not_reached(run, ["a.read"]) == []
    assert not_reached(run, []) == []


def test_feedback_names_the_stop_reason_and_missing_call():
    run = ObservationResult(scenario="X", callSequence=["cfg.isEnabled"],
                            consumerException="java.lang.ClassCastException: Foo cannot be cast to Bar")
    text = reach_feedback([ReturnValue(target="cfg.isEnabled", returns="true")], run, ["repo.save"])
    assert "cfg.isEnabled → true" in text
    assert "ClassCastException" in text
    assert "Never called: repo.save" in text


def test_parse_drops_invented_targets_and_types():
    analysis = ConsumerAnalysis(className="C", methodName="m", eventType="E",
                                dependencies=[DependencyInfo(name="repo", type="Repo")])
    raw = '''[{"target": "event.getEntityName", "returns": "STRING:collectionsheet"},
              {"target": "ghost.call", "returns": "true"},
              {"target": "repo.find", "returns": "MOCK:NotInSource"},
              {"target": "repo.find", "returns": "MOCK:Meeting"}]'''
    values = _parse(raw, analysis, "class C { Meeting m; }")
    assert [(v.target, v.returns) for v in values] == [
        ("event.getEntityName", "STRING:collectionsheet"), ("repo.find", "MOCK:Meeting")]


def test_reuse_picks_smallest_setup_that_reached_every_target():
    reached = ObservationResult(scenario="B", observations=[E(target="repo.save", callCount=1)])
    missed = ObservationResult(scenario="B", observations=[E(target="repo.save", callCount=0)])
    failed = ObservationResult(scenario="B", status="ERROR",
                               observations=[E(target="repo.save", callCount=1)])
    one = [ReturnValue(target="event.getName", returns="STRING:x")]
    two = one + [ReturnValue(target="repo.find", returns="NULL")]
    assert reusable_setup(["repo.save"], [(two, reached), (one, reached)]) == one
    assert reusable_setup(["repo.save"], [(one, missed), (two, failed)]) is None
    assert reusable_setup(["repo.save", "repo.publish"], [(one, reached)]) is None
    assert reusable_setup([], [(one, reached)]) is None


def test_forget_plan_drops_only_that_retry_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(path_setup, "_CACHE_DIR", tmp_path)
    analysis = ConsumerAnalysis(className="C", methodName="m", eventType="E",
                                dependencies=[DependencyInfo(name="repo", type="Repo")])
    scenario = sc(faults=[FaultInjection(dep="repo", method="save", fault="THROW")])
    retry = path_setup._cache_file(path_setup._prompt(analysis, "src", scenario, "fb"))
    first = path_setup._cache_file(path_setup._prompt(analysis, "src", scenario, None))
    retry.write_text("[]")
    first.write_text("[]")
    forget_plan(analysis, "src", scenario, "fb")
    assert not retry.exists() and first.exists()


def test_parse_ignores_type_names_inside_strings_and_comments():
    analysis = ConsumerAnalysis(className="C", methodName="m", eventType="E",
                                dependencies=[DependencyInfo(name="repo", type="Repo")])
    src = 'class C { String m = "No Calendar found"; // Calendar\n /* Calendar */ Meeting x; }'
    raw = '[{"target": "repo.find", "returns": "MOCK:Calendar"}, {"target": "repo.get", "returns": "MOCK:Meeting"}]'
    assert [v.returns for v in _parse(raw, analysis, src)] == ["MOCK:Meeting"]
