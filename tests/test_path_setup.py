"""Path setup: which calls a scenario targets, whether a run reached them, and the retry feedback."""
from eventbreaker.agent.path_setup import _parse, not_reached, reach_feedback, target_calls
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
