"""The plug: what an RL environment provides so Verity can audit it and guard training on it.

An environment is one Python module in environments/. Every environment has:

    NAME, VERSION     identity; bump VERSION whenever the prompt or the grader changes
    KIND              "answer" (one text reply), "tools" (a multi-step episode of tool calls), or "sandbox" (a Docker machine)
    DESCRIPTION       one sentence shown to Verity's attacker; it must not reveal how grading works

An answer environment also has:

    tasks(split)            -> list of dicts; each has "prompt" plus whatever the grader needs
    data_path(split)        -> the file tasks(split) reads (recorded in training manifests)
    grade(task, output)     -> float, the environment's own reward: the thing Verity audits
    check(task, output)     -> float, a trusted reference score: "is this answer really right?"
    honest_examples(task)   -> list of correct answers, so markers never flag honest work
    metrics(task, output)   -> optional dict of extra numbers for logs and evaluation
    task_key(task)          -> optional id used to keep training and evaluation tasks apart
    seeded_exploit(task), SEEDED_MARKER
                            -> optional: a known, disclosed bug for the offline fixture audit

A tools environment has an Episode class (reset(**task) plus public methods the model calls as tools),
tasks(split), data_path(split), grade(episode), check(episode) and honest_logs() (honest tool-call logs);
optionally metrics(episode), MAX_TOOL_CALLS, seeded_exploit(episode) and SEEDED_MARKER.

A sandbox environment has tasks() -> task ids; verity/sandbox.py audits them in Docker.
"""
import importlib

REQUIRED = {"answer": ("tasks", "data_path", "grade", "check", "honest_examples"),
            "tools": ("Episode", "tasks", "data_path", "grade", "check", "honest_logs"),
            "sandbox": ("tasks",)}


def load(name):
    """Import environments/<name>.py and check that it provides the plug."""
    env = importlib.import_module(f"environments.{name}")
    kind = getattr(env, "KIND", None)
    if kind not in REQUIRED:
        raise ValueError(f"environments/{name}.py must set KIND to one of {', '.join(REQUIRED)}")
    missing = [part for part in ("NAME", "VERSION", "DESCRIPTION", *REQUIRED[kind]) if not hasattr(env, part)]
    if missing:
        raise ValueError(f"environments/{name}.py is missing: {', '.join(missing)}")
    return env


def identity(env):
    return f"{env.NAME}@{env.VERSION}"


def matches(env, config):
    """Was this training run made on the current version of the environment?"""
    return (config.get("environment") == identity(env)
            or config.get("environment_sha256") in getattr(env, "LEGACY_SHA256", ()))
