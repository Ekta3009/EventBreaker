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


# Synthetic device-message listener: onMessage(Message message, @Header String type, @Header String tenant)
HEADERS = ConsumerAnalysis(
    className="DeviceListener", methodName="onMessage", eventType="Message",
    dependencies=[DependencyInfo(name="devices", type="DeviceRepo")],
    parameters=["final Message message", '@Header(name = "type", required = false) final String type',
                "@Header(name = \"tenant\") final String tenant"],
)


def test_plain_listener_parameters_are_settable_as_param_targets():
    from eventbreaker.scenarios.executor import _listener_arguments, value_params
    assert [(p.name, p.type) for p in value_params(HEADERS)] == [("type", "String"), ("tenant", "String")]
    assert _listener_arguments(HEADERS)[1] == "event, __eb_type, __eb_tenant"
    values = _parse('[{"target": "param.type", "returns": "STRING:EVENT"}, {"target": "param.ghost.x", "returns": "1"}]',
                    HEADERS, "")
    assert ("param.type", "STRING:EVENT") in [(v.target, v.returns) for v in values]


def test_param_section_only_for_listeners_with_plain_parameters():
    scenario = sc(dep="devices", method="save")
    assert "param.type: String" in path_setup._prompt(HEADERS, "src", scenario, None)
    plain = ConsumerAnalysis(className="C", methodName="m", eventType="E",
                             dependencies=[DependencyInfo(name="devices", type="DeviceRepo")])
    assert "param." not in path_setup._prompt(plain, "src", scenario, None)
    assert "param.x" not in [v.target for v in _parse('[{"target": "param.x", "returns": "1"}]', plain, "")]


def test_list_values_accepted_for_scalars_only():
    from eventbreaker.agent.path_setup import _VALUE
    assert _VALUE.match("LIST:STRING:https://example.com/hook") and _VALUE.match("LIST:3")
    assert not _VALUE.match("LIST:MOCK:Foo") and not _VALUE.match("LIST:NULL")


def test_deeper_setup_only_adds_downstream_work():
    from eventbreaker.agent.path_setup import deeper_setup
    def run(*called):
        return ObservationResult(scenario="B", observations=[E(target=t, callCount=1) for t in called])
    empty = []
    url = [ReturnValue(target="tenantApi.getValues", returns="LIST:STRING:https://x")]
    other = [ReturnValue(target="event.getType", returns="ENUM:B")]
    shallow = run("tenantApi.getValues", "mapper.write")
    deep = run("tenantApi.getValues", "mapper.write", "http.send")            # superset: same path, more work
    branch = run("tenantApi.getValues", "audit.log", "http.send", "x.y")      # more calls, but a different path
    tried = [(empty, shallow), (url, deep), (other, branch)]
    assert deeper_setup(["tenantApi.getValues"], empty, tried) == url
    assert deeper_setup(["tenantApi.getValues"], url, tried) is None          # already the deepest
    assert deeper_setup(["http.other"], empty, tried) is None                 # must still reach the target


def test_targets_written_with_arguments_are_normalised():
    a = ConsumerAnalysis(className="L", methodName="on", eventType="Message",
                         dependencies=[DependencyInfo(name="repo", type="Repo")])
    raw = """[{"target": "event.getMessageProperties.getHeaders.get(\\"topic\\")", "returns": "STRING:UPDATE"},
              {"target": "repo.find(event.getId()).getStatus()", "returns": "ENUM:ACTIVE"}]"""
    assert [v.target for v in _parse(raw, a, "class L {}")] == [
        "event.getMessageProperties.getHeaders.get", "repo.find.getStatus"]


def test_retry_feedback_lists_exact_default_answer_names():
    run = ObservationResult(
        scenario="B", consumerThrew=True, consumerException="AmqpRejectAndDontRequeueException: Tenant t is soft deleted!",
        defaultAnswers=["SystemManagementHolder.getInstance = a fake SystemManagement",
                        "SystemManagementHolder.getInstance.isTenantSoftDeleted = true"])
    text = reach_feedback([ReturnValue(target="SystemManagementHolder.isTenantSoftDeleted", returns="false")],
                          run, ["controllerManagement.findOrRegisterTargetIfItDoesNotExist"])
    assert "EXACT override target names" in text
    assert "  SystemManagementHolder.getInstance.isTenantSoftDeleted = true" in text


def test_merge_keeps_first_attempt_values_retry_wins():
    from eventbreaker.agent.path_setup import merge_setups
    first = [ReturnValue(target="param.type", returns="STRING:EVENT"),
             ReturnValue(target="holder.isDeleted", returns="true")]
    retry = [ReturnValue(target="holder.getInstance.isDeleted", returns="false"),
             ReturnValue(target="holder.isDeleted", returns="false")]
    assert [(v.target, v.returns) for v in merge_setups(first, retry)] == [
        ("param.type", "STRING:EVENT"), ("holder.getInstance.isDeleted", "false"), ("holder.isDeleted", "false")]
