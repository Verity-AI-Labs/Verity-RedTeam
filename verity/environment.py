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

A tools environment also has:

    Episode                 a class: reset(**task) starts an episode and may return the first observation (the
                            trainer appends it to the prompt); its public methods are the tools the model calls
    tasks(split), data_path(split)
    grade(episode), check(episode)
    honest_logs()           -> records of honest play, mid-episode ones included (Guard checks the record after
                               every call), so markers never flag honest play
    record(episode)         -> optional: the episode as text, which is what markers read (default: episode.log,
                               one call per line)

Either kind may add:

    metrics(...)            -> dict of extra numbers for logs and evaluation: metrics(task, output) or metrics(episode)
    summarize(rows)         -> averages a batch of metrics() rows (default: plain means)
    task_key(task)          -> an id that keeps training and evaluation tasks apart
    SEEDED_MARKERS, seeded_exploit(task or episode, marker_id)
                            -> known, disclosed bugs and their markers, for the offline fixture audit; seeded_exploit
                               returns None where a bug does not apply
    AUDIT_TASKS             -> how many tasks (answer) or episodes (tools) a live audit shows the attacker
    RECORD                  -> one sentence telling the attacker how an episode is written down for markers
    MAX_TOOL_CALLS, done(episode), DEPLOYMENT
                            -> tools: the call cap, when an episode is over, and what a deployed agent gets:
                               {"tools": names, "prompt": prompt(task), "split": optional held-out split}

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


def record(env, episode):
    """A tools episode as text, the way markers read it."""
    return env.record(episode) if hasattr(env, "record") else "\n".join(episode.log)


def summarize(env, rows):
    """Means of a batch of metrics() rows; the environment can average its own (e.g. skipping missing values)."""
    if hasattr(env, "summarize"):
        return env.summarize(rows)
    keys = sorted({key for row in rows for key in row if isinstance(row[key], (int, float))})
    return {key: sum(float(row.get(key) or 0) for row in rows) / len(rows) for key in keys} if rows else {}


def matches(env, config):
    """Was this training run made on the current version of the environment?"""
    return (config.get("environment") == identity(env)
            or config.get("environment_sha256") in getattr(env, "LEGACY_SHA256", ()))
