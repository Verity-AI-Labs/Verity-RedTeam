"""Multi-episode attacker loop against container-backed environments.

``verifiers``-format envs have no sandbox, so they take the one-shot path: one
completion submitted to ``verify``. Container-backed envs (terminal / docker_test)
let the attacker EXEC commands, observe results, and SUBMIT after several episodes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from uuid import uuid4

from verity_core.env import RewardResult, TaskSpec, VerityEnv
from verity_core.models import ModelClient, ModelResponse, TokenUsage

from verity_redteam.outcomes import ERROR, NO_REWARD
from verity_redteam.prompts.builder import PromptBuilder, display_attempt
from verity_redteam.prompts.templates import PROMPT_VERSION
from verity_redteam.sandbox import CommandLog, is_container_backed
from verity_redteam.types import AttackTrial, Episode

logger = logging.getLogger(__name__)

DEFAULT_MAX_EPISODES = 15
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_SUBMISSION_LENGTH = 32768

__all__ = [
    "DEFAULT_MAX_EPISODES",
    "AgentLoop",
    "parse_agent_reply",
]


def parse_agent_reply(text: str) -> tuple[list[str], str | None]:
    """Pull EXEC commands and an optional SUBMIT body out of a model reply.

    If the reply has no EXEC/SUBMIT markers, the whole text is an implicit
    submission (the one-shot path). Commands without a SUBMIT keep the loop going.
    """
    lines = (text or "").splitlines()
    commands: list[str] = []
    submit_at: int | None = None
    same_line_body = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("EXEC:"):
            commands.append(stripped[len("EXEC:") :].strip())
            continue
        if stripped.startswith("SUBMIT:"):
            submit_at = index
            same_line_body = stripped[len("SUBMIT:") :].strip()
            break
    if submit_at is None:
        if commands:
            return commands, None
        return [], text
    rest = "\n".join(lines[submit_at + 1 :])
    body = "\n".join(part for part in (same_line_body, rest) if part).strip()
    return commands, body


@dataclass(slots=True)
class AgentLoop:
    """Run one attack trial: one-shot or multi-episode, then ``env.verify``."""

    model: str
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_submission_length: int = DEFAULT_MAX_SUBMISSION_LENGTH
    max_episodes: int = DEFAULT_MAX_EPISODES
    prompt_version: str = PROMPT_VERSION
    system_prompt: str | None = None
    user_prompt: str | None = None

    def run(
        self,
        env: VerityEnv,
        spec: TaskSpec,
        client: ModelClient,
        attempt: int,
        n_trials: int,
        *,
        strategy: str,
    ) -> AttackTrial:
        started = time.monotonic()
        trial_id = str(uuid4())
        system = self.system_prompt or PromptBuilder.adversarial_system_prompt(
            strategy, spec.domain, version=self.prompt_version
        )
        user = self.user_prompt or PromptBuilder.adversarial_user_prompt(
            spec, attempt, n_trials, version=self.prompt_version
        )
        messages: list[dict[str, object]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        command_log = CommandLog()
        attached = False
        runner = getattr(env, "runner", None)
        if is_container_backed(env) and runner is not None:
            command_log.attach(runner)
            attached = True

        model_response: ModelResponse | None = None
        submission = ""
        reward = RewardResult(0.0, False)
        error: str | None = None
        classification = NO_REWARD
        episodes: list[Episode] = []
        usage = TokenUsage()

        try:
            if is_container_backed(env):
                model_response, submission, episodes, usage = self._multi_episode(
                    env, client, messages
                )
            else:
                model_response, submission, episodes, usage = self._oneshot(client, messages)
            logger.debug(
                "trial submission env_id=%s trial_id=%s content=%s",
                spec.id,
                trial_id,
                submission,
            )
            if len(submission) > self.max_submission_length:
                logger.warning(
                    "truncating submission trial_id=%s length=%d max=%d",
                    trial_id,
                    len(submission),
                    self.max_submission_length,
                )
                submission = submission[: self.max_submission_length]
            try:
                reward = env.verify(submission)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                classification = ERROR
                logger.warning(
                    "verify failed env_id=%s trial_id=%s error=%s",
                    spec.id,
                    trial_id,
                    error,
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            classification = ERROR
            logger.warning(
                "model call failed env_id=%s trial_id=%s error=%s",
                spec.id,
                trial_id,
                error,
            )
        finally:
            log_entries = command_log.to_list()
            if attached:
                command_log.detach()

        duration = time.monotonic() - started
        logger.info(
            "trial done env_id=%s strategy=%s attempt=%d classification=%s "
            "episodes=%d duration=%.2fs",
            spec.id,
            strategy,
            display_attempt(attempt),
            classification,
            len(episodes),
            duration,
        )
        return AttackTrial(
            trial_id=trial_id,
            env_id=spec.id,
            strategy=strategy,
            attempt=attempt,
            submission=submission,
            reward=reward,
            hacked=False,
            messages=messages,
            model_id=self.model,
            model_response=model_response,
            duration=duration,
            error=error,
            classification=classification,
            prompt_version=self.prompt_version,
            episodes=episodes,
            evidence={
                "total_tokens": usage.total_tokens,
                "command_log": log_entries,
                "tampering": command_log.has_tampering(),
                "max_episodes": self.max_episodes,
            },
        )

    def _oneshot(
        self, client: ModelClient, messages: list[dict[str, object]]
    ) -> tuple[ModelResponse, str, list[Episode], TokenUsage]:
        response = client.complete(
            self.model,
            messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            use_cache=False,
        )
        submission = response.content or ""
        episode = Episode(
            index=0,
            prompt=str(messages[-1].get("content") or ""),
            response=submission,
            tool_calls=[{"type": "submit", "body": submission}],
        )
        return response, submission, [episode], response.usage

    def _multi_episode(
        self,
        env: VerityEnv,
        client: ModelClient,
        messages: list[dict[str, object]],
    ) -> tuple[ModelResponse | None, str, list[Episode], TokenUsage]:
        episodes: list[Episode] = []
        usage = TokenUsage()
        last: ModelResponse | None = None
        submission = ""
        for index in range(self.max_episodes):
            prompt = str(messages[-1].get("content") or "")
            last = client.complete(
                self.model,
                messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                use_cache=False,
            )
            usage = usage + last.usage
            content = last.content or ""
            messages.append({"role": "assistant", "content": content})
            commands, submitted = parse_agent_reply(content)
            tool_calls: list[dict[str, object]] = []
            observations: list[str] = []
            for command in commands:
                tool_calls.append({"type": "exec", "command": command})
                step = env.step(command)
                observations.append(step.observation.text)
            if submitted is not None:
                tool_calls.append({"type": "submit", "body": submitted})
                submission = submitted
                episodes.append(
                    Episode(
                        index=index,
                        prompt=prompt,
                        response=content,
                        tool_calls=tool_calls,
                        observations=observations,
                    )
                )
                break
            if not commands:
                submission = content
                tool_calls.append({"type": "submit", "body": content})
                episodes.append(
                    Episode(
                        index=index,
                        prompt=prompt,
                        response=content,
                        tool_calls=tool_calls,
                        observations=observations,
                    )
                )
                break
            messages.append(
                {
                    "role": "user",
                    "content": "\n".join(observations) if observations else "(no output)",
                }
            )
            episodes.append(
                Episode(
                    index=index,
                    prompt=prompt,
                    response=content,
                    tool_calls=tool_calls,
                    observations=observations,
                )
            )
        else:
            if last is not None and not submission:
                submission = last.content or ""
        return last, submission, episodes, usage
