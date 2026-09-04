"""Tests for the multi-episode attacker loop."""

from __future__ import annotations

from tests.conftest import FakeClient, FakeEnv, FakeRunner, make_spec

from verity_redteam.agent import AgentLoop, parse_agent_reply
from verity_redteam.outcomes import NO_REWARD


class TestParseAgentReply:
    def test_implicit_submission_when_there_are_no_markers(self) -> None:
        commands, submitted = parse_agent_reply("just a patch")
        assert commands == []
        assert submitted == "just a patch"

    def test_exec_without_submit_keeps_the_loop_open(self) -> None:
        commands, submitted = parse_agent_reply("EXEC: ls /app\nEXEC: cat tests/test.py")
        assert commands == ["ls /app", "cat tests/test.py"]
        assert submitted is None

    def test_submit_body_can_follow_execs(self) -> None:
        text = "EXEC: ls\nSUBMIT:\nplaceholder\n"
        commands, submitted = parse_agent_reply(text)
        assert commands == ["ls"]
        assert submitted == "placeholder"

    def test_submit_body_on_the_same_line(self) -> None:
        commands, submitted = parse_agent_reply("SUBMIT: inline")
        assert commands == []
        assert submitted == "inline"


class TestOneShot:
    def test_verifiers_envs_submit_the_raw_completion(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, passing="bypass")
        client = FakeClient(contents="bypass")
        trial = AgentLoop(model="test-model").run(
            env,
            spec,
            client,
            0,
            8,
            strategy="freeform",  # type: ignore[arg-type]
        )
        assert trial.submission == "bypass"
        assert trial.classification == NO_REWARD
        assert trial.hacked is False
        assert len(trial.episodes) == 1
        assert trial.episodes[0].tool_calls[0]["type"] == "submit"
        assert env.verify_calls == ["bypass"]
        assert client.calls[0]["use_cache"] is False


class TestMultiEpisode:
    def test_exec_then_submit_records_the_trajectory(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        env.entry = {"format": "terminal"}
        env.runner = FakeRunner()
        client = FakeClient(contents=["EXEC: ls /app", "SUBMIT:\nbypass"])
        trial = AgentLoop(model="test-model", max_episodes=4).run(
            env,
            spec,
            client,
            0,
            1,
            strategy="freeform",  # type: ignore[arg-type]
        )
        assert trial.submission == "bypass"
        assert len(trial.episodes) == 2
        assert trial.episodes[0].tool_calls == [{"type": "exec", "command": "ls /app"}]
        assert trial.episodes[0].observations == ["observed:ls /app"]
        assert trial.episodes[1].tool_calls[-1] == {"type": "submit", "body": "bypass"}
        assert env.runner.seen == ["ls /app"]
        assert env.runner.exec.__func__ is FakeRunner.exec
        assert env.verify_calls == ["bypass"]
        assert trial.evidence["total_tokens"] == 12
        assert trial.evidence["command_log"][0]["command"] == "ls /app"

    def test_caps_episodes_and_submits_the_last_reply(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="never")
        env.entry = {"format": "terminal"}
        env.runner = FakeRunner()
        client = FakeClient(contents="EXEC: echo again")
        trial = AgentLoop(model="test-model", max_episodes=3).run(
            env,
            spec,
            client,
            0,
            1,
            strategy="freeform",  # type: ignore[arg-type]
        )
        assert len(trial.episodes) == 3
        assert trial.submission == "EXEC: echo again"
        assert env.runner.seen == ["echo again", "echo again", "echo again"]
