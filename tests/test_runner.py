"""The agent-runner interface, the Claude Code adapter and the fake (ADLC-012)."""

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adlc.commands import CommandError
from adlc.config import Models
from adlc.fake_runner import FakeRunner, error, ok, rate_limited, timeout
from adlc.runner import (
    AgentRequest,
    AgentResult,
    ClaudeCodeRunner,
    Role,
    same_model,
)

STUB = """\
import json, os, sys, time
from pathlib import Path

here = Path(__file__).parent
scenario = json.loads((here / "scenario.json").read_text())
args = sys.argv[1:]
record = {"args": args, "stdin": sys.stdin.read(), "cwd": os.getcwd()}
if "--append-system-prompt-file" in args:
    path = Path(args[args.index("--append-system-prompt-file") + 1])
    record["method"] = path.read_text(encoding="utf-8")
    record["method_path"] = str(path)
(here / "record.json").write_text(json.dumps(record))
if scenario.get("child"):
    import subprocess
    code = "import time, pathlib; time.sleep(3); pathlib.Path(r'%s').write_text('alive')"
    subprocess.Popen([sys.executable, "-c", code % (here / "child.txt")])
time.sleep(scenario.get("sleep", 0))
for line in scenario.get("lines", []):
    print(line if isinstance(line, str) else json.dumps(line), flush=True)
sys.stderr.write(scenario.get("stderr", ""))
sys.exit(scenario.get("exit", 0))
"""

MODELS = Models(coder="sonnet", tester="haiku", reviewer="opus", pr_writer="haiku")


def result_event(**over):
    event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "api_error_status": None,
        "result": "All done.",
        "num_turns": 3,
        "total_cost_usd": 0.0123,
        "terminal_reason": "completed",
        "session_id": "s-1",
        "permission_denials": [],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 40,
        },
        "modelUsage": {
            "claude-sonnet-5-5": {"inputTokens": 10, "outputTokens": 20, "costUSD": 0.01},
            "claude-haiku-5-5": {"inputTokens": 1, "outputTokens": 1, "costUSD": 0.0023},
        },
    }
    event.update(over)
    return event


class Stub:
    def __init__(self, tmp_path: Path):
        self.dir = tmp_path / "stub"
        self.dir.mkdir()
        (self.dir / "stub.py").write_text(STUB, encoding="utf-8")
        self.command = (sys.executable, str(self.dir / "stub.py"))
        self.work = tmp_path / "work"
        self.work.mkdir()

    def script(self, **scenario):
        (self.dir / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")

    def record(self) -> dict:
        return json.loads((self.dir / "record.json").read_text(encoding="utf-8"))

    def runner(self, **kwargs) -> ClaudeCodeRunner:
        return ClaudeCodeRunner(MODELS, command=self.command, **kwargs)

    def request(self, role=Role.CODER, **kwargs) -> AgentRequest:
        return AgentRequest(role=role, prompt="Do the thing.", cwd=self.work, **kwargs)


@pytest.fixture
def stub(tmp_path):
    return Stub(tmp_path)


def arg_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


# --- the command line ---------------------------------------------------------------------------


def test_a_call_is_a_fresh_headless_stream_with_project_settings_only(stub):
    stub.script(lines=[result_event()])
    stub.runner().run(stub.request())
    args = stub.record()["args"]
    assert "-p" in args
    assert arg_after(args, "--output-format") == "stream-json"
    assert "--verbose" in args
    assert arg_after(args, "--setting-sources") == "project"
    assert "--no-session-persistence" in args
    for forbidden in ("--resume", "--continue", "-c", "--session-id"):
        assert forbidden not in args


def test_the_prompt_goes_in_on_stdin_not_on_the_command_line(stub):
    stub.script(lines=[result_event()])
    stub.runner().run(stub.request())
    record = stub.record()
    assert record["stdin"] == "Do the thing."
    assert "Do the thing." not in record["args"]


def test_it_runs_in_the_requested_folder(stub):
    stub.script(lines=[result_event()])
    stub.runner().run(stub.request())
    assert Path(stub.record()["cwd"]).resolve() == stub.work.resolve()


@pytest.mark.parametrize(
    "role, model",
    [
        (Role.CODER, "sonnet"),
        (Role.TESTER, "haiku"),
        (Role.REVIEWER, "opus"),
        (Role.PR_WRITER, "haiku"),
    ],
)
def test_the_model_is_chosen_by_role(stub, role, model):
    stub.script(lines=[result_event()])
    stub.runner().run(stub.request(role))
    assert arg_after(stub.record()["args"], "--model") == model


def test_an_unset_model_means_no_model_flag(stub):
    stub.script(lines=[result_event()])
    runner = ClaudeCodeRunner(Models(None, None, None, None), command=stub.command)
    runner.run(stub.request(Role.CODER))
    assert "--model" not in stub.record()["args"]


def test_method_text_is_passed_in_a_temporary_file_that_is_removed_afterwards(stub):
    stub.script(lines=[result_event()])
    stub.runner().run(stub.request(method="Write the test first."))
    record = stub.record()
    assert record["method"] == "Write the test first."
    assert not Path(record["method_path"]).exists()


def test_no_method_means_no_prompt_file_flag(stub):
    stub.script(lines=[result_event()])
    stub.runner().run(stub.request())
    assert "--append-system-prompt-file" not in stub.record()["args"]


def test_the_temporary_file_is_removed_even_when_the_call_fails(stub):
    stub.script(lines=[], exit=3)
    stub.runner().run(stub.request(method="text"))
    assert not Path(stub.record()["method_path"]).exists()


def test_agent_and_tool_lists_are_passed_exactly_as_given(stub):
    stub.script(lines=[result_event()])
    stub.runner().run(
        stub.request(
            agent="coder", allowed_tools=("Read", "Edit"), disallowed_tools=("Bash(rm:*)",)
        )
    )
    args = stub.record()["args"]
    assert arg_after(args, "--agent") == "coder"
    assert arg_after(args, "--allowedTools") == "Read,Edit"
    assert arg_after(args, "--disallowedTools") == "Bash(rm:*)"


def test_no_tool_flags_are_added_when_none_are_given(stub):
    stub.script(lines=[result_event()])
    stub.runner().run(stub.request())
    args = stub.record()["args"]
    for flag in ("--agent", "--allowedTools", "--disallowedTools", "--max-budget-usd"):
        assert flag not in args


def test_a_cost_limit_becomes_a_budget_flag(stub):
    stub.script(lines=[result_event()])
    stub.runner(max_cost_usd=2.5).run(stub.request())
    assert arg_after(stub.record()["args"], "--max-budget-usd") == "2.5"


# --- the independent reviewer -------------------------------------------------------------------


def test_same_model_matches_ids_and_aliases():
    assert same_model("sonnet", "sonnet")
    assert same_model("Sonnet", "claude-sonnet-5-5")
    assert same_model("claude-sonnet-5-5", "sonnet")
    assert not same_model("sonnet", "opus")
    assert not same_model("claude-sonnet-5-5", "claude-opus-5-5")
    assert not same_model(None, "sonnet")


def test_a_reviewer_on_the_coders_model_is_refused_before_anything_runs(stub):
    stub.script(lines=[result_event()])
    runner = ClaudeCodeRunner(
        Models("sonnet", None, "claude-sonnet-5-5", None), command=stub.command
    )
    with pytest.raises(CommandError, match="reviewer"):
        runner.run(stub.request(Role.REVIEWER))
    assert not (stub.dir / "record.json").exists()


def test_a_reviewer_with_no_model_is_refused(stub):
    runner = ClaudeCodeRunner(Models("sonnet", None, None, None), command=stub.command)
    with pytest.raises(CommandError, match="reviewer"):
        runner.run(stub.request(Role.REVIEWER))


def test_other_roles_may_share_the_coders_model(stub):
    stub.script(lines=[result_event()])
    runner = ClaudeCodeRunner(Models("sonnet", "sonnet", "opus", "sonnet"), command=stub.command)
    assert runner.run(stub.request(Role.TESTER)).status == "ok"


def test_the_primary_model_is_the_one_that_cost_most(stub):
    stub.script(lines=[result_event()])
    result = stub.runner().run(stub.request())
    assert result.primary_model == "claude-sonnet-5-5"
    assert set(result.models) == {"claude-sonnet-5-5", "claude-haiku-5-5"}


def test_no_model_usage_means_no_primary_model(stub):
    stub.script(lines=[result_event(modelUsage={})])
    assert stub.runner().run(stub.request()).primary_model is None


# --- reading the result -------------------------------------------------------------------------


def test_a_good_run_is_read_into_the_result(stub):
    stub.script(
        lines=[
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {}},
            result_event(permission_denials=[{"tool_name": "Bash"}]),
        ]
    )
    result = stub.runner().run(stub.request())
    assert result.status == "ok"
    assert result.text == "All done."
    assert result.turns == 3
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 20
    assert result.usage.cache_read_tokens == 30
    assert result.usage.cache_creation_tokens == 40
    assert result.usage.cost_usd_list == pytest.approx(0.0123)
    assert result.permission_denials == ({"tool_name": "Bash"},)
    assert result.rate_limit is None
    assert result.raw["session_id"] == "s-1"


def test_elapsed_time_is_measured_by_the_runner(stub):
    stub.script(lines=[result_event(duration_ms=999999)], sleep=0.3)
    result = stub.runner().run(stub.request())
    assert 0.2 <= result.duration < 30


def test_unknown_extra_events_and_blank_lines_are_ignored(stub):
    stub.script(lines=["", "not json at all", {"type": "mystery"}, result_event()])
    assert stub.runner().run(stub.request()).status == "ok"


def test_an_error_result_is_an_error_not_a_pass(stub):
    stub.script(
        lines=[result_event(is_error=True, subtype="error_during_execution", result="boom")]
    )
    result = stub.runner().run(stub.request())
    assert result.status == "error"
    assert "boom" in result.detail


def test_a_run_that_stopped_for_another_reason_is_an_error(stub):
    stub.script(lines=[result_event(subtype="error_max_turns", terminal_reason="max_turns")])
    result = stub.runner().run(stub.request())
    assert result.status == "error"
    assert "max_turns" in result.detail


def test_a_missing_result_event_is_an_error_with_the_stderr_tail(stub):
    stub.script(lines=[{"type": "system"}], stderr="Traceback: it broke\n", exit=0)
    result = stub.runner().run(stub.request())
    assert result.status == "error"
    assert "Traceback: it broke" in result.detail


def test_a_non_zero_exit_is_an_error_even_with_a_good_looking_result(stub):
    stub.script(lines=[result_event()], exit=2, stderr="crashed")
    result = stub.runner().run(stub.request())
    assert result.status == "error"
    assert result.exit_code == 2
    assert "crashed" in result.detail


def test_a_missing_executable_is_a_clear_error(tmp_path):
    runner = ClaudeCodeRunner(MODELS, command=(str(tmp_path / "no-such-claude"),))
    request = AgentRequest(role=Role.CODER, prompt="x", cwd=tmp_path)
    with pytest.raises(CommandError, match="claude"):
        runner.run(request)


def test_a_hung_call_is_killed_and_reported_as_a_timeout(stub):
    stub.script(lines=[result_event()], sleep=60)
    started = time.monotonic()
    result = stub.runner().run(stub.request(timeout=0.5))
    assert time.monotonic() - started < 30
    assert result.status == "timeout"
    assert result.text == ""


def test_the_raw_stream_is_saved_when_a_log_path_is_given(stub, tmp_path):
    stub.script(lines=[{"type": "system"}, result_event()])
    log = tmp_path / "logs" / "call.jsonl"
    stub.runner().run(stub.request(log_path=log))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["type"] == "system"
    assert json.loads(lines[-1])["type"] == "result"


# --- usage limits -------------------------------------------------------------------------------

RESET = 1893456000  # 2030-01-01T00:00:00Z


def limit_event(status="rejected", kind="five_hour", resets=RESET, overage=False):
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "resetsAt": resets,
            "rateLimitType": kind,
            "isUsingOverage": overage,
        },
    }


def test_a_rejected_rate_limit_event_is_a_rate_limited_result(stub):
    stub.script(lines=[limit_event(), result_event(is_error=True, result="x")], exit=1)
    result = stub.runner().run(stub.request())
    assert result.status == "rate_limited"
    assert result.rate_limit.limit_type == "five_hour"
    assert result.rate_limit.resets_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert result.rate_limit.status == "rejected"


def test_a_seven_day_limit_is_reported_as_such(stub):
    stub.script(lines=[limit_event(kind="seven_day")], exit=1)
    result = stub.runner().run(stub.request())
    assert (result.status, result.rate_limit.limit_type) == ("rate_limited", "seven_day")


def test_a_429_error_result_is_rate_limited_even_without_an_event(stub):
    stub.script(
        lines=[result_event(is_error=True, api_error_status=429, result="slow down")], exit=1
    )
    result = stub.runner().run(stub.request())
    assert result.status == "rate_limited"
    assert result.rate_limit.resets_at is None


@pytest.mark.parametrize(
    "text", ["You've hit your limit", "Usage limit reached|1893456000", "rate limit exceeded"]
)
def test_error_text_about_a_limit_is_rate_limited(stub, text):
    stub.script(lines=[result_event(is_error=True, result=text)], exit=1)
    assert stub.runner().run(stub.request()).status == "rate_limited"


def test_the_words_in_a_successful_answer_do_not_count(stub):
    stub.script(
        lines=[result_event(result="I added a rate limit and a usage limit reached check.")]
    )
    result = stub.runner().run(stub.request())
    assert result.status == "ok"


def test_a_warning_event_is_information_not_a_stop(stub):
    stub.script(lines=[limit_event(status="allowed_warning"), result_event()])
    result = stub.runner().run(stub.request())
    assert result.status == "ok"
    assert result.rate_limit.status == "allowed_warning"


def test_an_allowed_event_is_not_a_limit(stub):
    stub.script(lines=[limit_event(status="allowed"), result_event()])
    result = stub.runner().run(stub.request())
    assert result.status == "ok"


def test_a_limit_with_no_result_event_still_counts(stub):
    stub.script(lines=[], stderr="Error: You've hit your limit", exit=1)
    assert stub.runner().run(stub.request()).status == "rate_limited"


def test_a_rate_limited_call_leaves_a_sample_for_the_first_real_one(stub, tmp_path):
    stub.script(lines=[limit_event(), result_event(is_error=True, result="x")], exit=1)
    sample = tmp_path / "logs" / "rate-limits.jsonl"
    stub.runner(rate_limit_log=sample).run(stub.request())
    stub.runner(rate_limit_log=sample).run(stub.request(Role.TESTER))
    rows = [json.loads(line) for line in sample.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["role"] == "coder" and rows[1]["role"] == "tester"
    assert rows[0]["events"][0]["type"] == "rate_limit_event"
    assert "at" in rows[0]


def test_a_good_call_leaves_no_sample(stub, tmp_path):
    stub.script(lines=[result_event()])
    sample = tmp_path / "logs" / "rate-limits.jsonl"
    stub.runner(rate_limit_log=sample).run(stub.request())
    assert not sample.exists()


# --- the fake -----------------------------------------------------------------------------------


def req(role=Role.CODER, tmp=Path(".")) -> AgentRequest:
    return AgentRequest(role=role, prompt="p", cwd=tmp)


def test_the_fake_returns_scripted_results_in_order_and_records_requests():
    fake = FakeRunner([ok("one"), error("bad"), timeout(), rate_limited(RESET)])
    results = [fake.run(req(Role.TESTER)), fake.run(req()), fake.run(req()), fake.run(req())]
    assert [r.status for r in results] == ["ok", "error", "timeout", "rate_limited"]
    assert results[0].text == "one"
    assert results[1].detail == "bad"
    assert results[3].rate_limit.resets_at == datetime.fromtimestamp(RESET, UTC)
    assert [r.role for r in fake.requests] == [Role.TESTER, Role.CODER, Role.CODER, Role.CODER]


def test_the_fake_fails_loudly_when_the_script_runs_out():
    fake = FakeRunner([ok()])
    fake.run(req())
    with pytest.raises(AssertionError, match="script"):
        fake.run(req())


def test_a_scripted_function_can_edit_files_and_see_the_request(tmp_path):
    def coder(request: AgentRequest) -> AgentResult:
        (request.cwd / "new.py").write_text("x = 1\n")
        return ok("wrote it")

    fake = FakeRunner([coder])
    result = fake.run(req(tmp=tmp_path))
    assert result.text == "wrote it"
    assert (tmp_path / "new.py").read_text() == "x = 1\n"


def test_the_fake_can_enforce_the_reviewer_rule_like_the_real_adapter():
    fake = FakeRunner([ok()], models=Models("sonnet", None, "sonnet", None))
    with pytest.raises(CommandError, match="reviewer"):
        fake.run(req(Role.REVIEWER))


def test_the_fake_needs_no_network_or_process(monkeypatch):
    import subprocess

    def no_process(*a, **k):
        raise AssertionError("the fake must not start a process")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    assert FakeRunner([ok()]).run(req()).status == "ok"


def test_a_timeout_kills_what_the_agent_started_too(stub):
    stub.script(lines=[], sleep=60, child=True)
    stub.runner().run(stub.request(timeout=1.0))
    time.sleep(4)
    assert not (stub.dir / "child.txt").exists()


def test_only_the_terminal_reason_can_mark_a_run_as_not_completed(stub):
    stub.script(lines=[result_event(terminal_reason="aborted")])
    assert stub.runner().run(stub.request()).status == "error"


def test_a_reset_time_in_milliseconds_is_understood(stub):
    stub.script(lines=[limit_event(resets=RESET * 1000)], exit=1)
    result = stub.runner().run(stub.request())
    assert result.rate_limit.resets_at == datetime(2030, 1, 1, tzinfo=UTC)


def test_is_error_alone_makes_a_run_an_error(stub):
    stub.script(lines=[result_event(is_error=True, result="odd")])
    assert stub.runner().run(stub.request()).status == "error"


@pytest.mark.skipif(os.environ.get("ADLC_LIVE") != "1", reason="set ADLC_LIVE=1 for a real call")
def test_live_one_cheap_call_to_the_real_claude(tmp_path):
    runner = ClaudeCodeRunner(Models(None, "haiku", None, None), max_cost_usd=0.5)
    request = AgentRequest(
        role=Role.TESTER,
        prompt="Reply with the single word OK.",
        cwd=tmp_path,
        timeout=180,
        log_path=tmp_path / "live.jsonl",
    )
    result = runner.run(request)
    assert result.status == "ok", result.detail
    assert "OK" in result.text
    assert result.usage.output_tokens > 0 and result.usage.cost_usd_list > 0
    assert "haiku" in (result.primary_model or "")
    assert (tmp_path / "live.jsonl").stat().st_size > 0
