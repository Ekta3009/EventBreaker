from eventbreaker.agent.nemotron import _last_object_array


def test_answer_after_reasoning_with_brackets_is_found():
    # Run 14 (Graylog UrlAllowlistService): prose with code brackets before the answer.
    raw = ('Thinking: `x[0]` and [1, 2] ... ```java\nint[] a = new int[3];\n```\n'
           'Answer:\n[{"scenarioType": "DUPLICATE", "testConfig": {"callPattern": "DUPLICATE"}}]\nDone [ok].')
    assert _last_object_array(raw) == [{"scenarioType": "DUPLICATE", "testConfig": {"callPattern": "DUPLICATE"}}]


def test_no_object_array_returns_none():
    assert _last_object_array("only [1, 2] and [] here") is None
