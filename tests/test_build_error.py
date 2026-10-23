"""Build errors: one-line summary in the terminal, compiler output kept for the report."""
from eventbreaker.models.observation import BUILD_DETAIL_MARKER
from eventbreaker.scenarios.executor import _extract_build_error

MISSING_CLASS = """\
[ERROR] COMPILATION ERROR :
[ERROR] /x/EventBreakerExecution.java:[53,9] cannot find symbol
  symbol:   class ApplicationReadyEvent
  location: class eventbreaker.generated.EventBreakerExecution
[ERROR] /x/EventBreakerExecution.java:[54,61] cannot find symbol
  symbol:   class ApplicationReadyEvent
  location: class eventbreaker.generated.EventBreakerExecution
"""


def test_missing_class_is_named_in_the_summary():
    summary, _, detail = _extract_build_error(MISSING_CLASS).partition(BUILD_DETAIL_MARKER)
    assert summary.startswith("Could not build a test harness: `ApplicationReadyEvent` is not on")
    assert "--classpath" in summary
    assert "[53,9] cannot find symbol" in detail          # compiler output kept for the report


def test_several_missing_classes_listed_once_each():
    output = MISSING_CLASS + "  symbol:   class TenantContext\n[ERROR] x cannot find symbol\n"
    summary = _extract_build_error(output).partition(BUILD_DETAIL_MARKER)[0]
    assert "`ApplicationReadyEvent`, `TenantContext` are not on" in summary


def test_other_build_errors_keep_their_output():
    summary, _, detail = _extract_build_error("[ERROR] something else broke\n").partition(BUILD_DETAIL_MARKER)
    assert summary == "Could not build a test harness."
    assert "something else broke" in detail


def test_run_without_result_explains_and_shows_last_output():
    from eventbreaker.scenarios.executor import _no_result_message
    msg = _no_result_message("WARNING: agent loaded\nStarting validator\nchecking cas.server.name\n")
    assert "never finished its scenario" in msg and "180 s" in msg
    assert msg.endswith("Last output:\nStarting validator\nchecking cas.server.name")
    assert _no_result_message("").endswith("It printed nothing.")
