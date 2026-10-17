"""Terminal Wrench: 13 sandboxed environments plugged into Verity.

Each task is a Docker machine with an instruction, a reference solution and a grader (tasks/<id>/,
listed in tasks.json). The model acts through shell commands, so Verity audits these with its sandbox
engine (verity/sandbox.py): grader checks, an AI attacker, and traced proof for every hack it finds.
"""
import json
from pathlib import Path

from verity import runner

NAME, VERSION, KIND = "terminal_wrench", 1, "sandbox"
DESCRIPTION = "sandboxed terminal tasks: the agent runs shell commands in a Docker machine and a grader checks the result"


def tasks():
    return list(runner.TASKS)


def replay(trajectory, guard, stop_on_block=True):
    """Screen a recorded episode command by command. Executes nothing; stops at the first block."""
    rows = (json.loads(line) for line in Path(trajectory).read_text().splitlines() if line.strip())
    events = []
    for row in rows:
        command = row.get("action", {}).get("command", "")
        if not command:
            continue
        matches = guard.check(command, "command")
        blocked = any(marker.get("on_match", {}).get("block", False) for marker in matches)
        event = {"turn": row.get("turn"), "command": command, "blocked": blocked,
                 "signature_ids": [marker["id"] for marker in matches],
                 "warnings": [marker.get("on_match", {}).get("warn", "") for marker in matches],
                 "source": "recorded_replay", "executed": False}
        if not blocked:
            event["recorded_observation"] = row.get("observation", {})
        events.append(event)
        if blocked and stop_on_block:
            break
    return events
