#!/usr/bin/env python3
"""Recompute material mutation survivors and hackability for an existing audit task folder, offline.

    python3 scripts/rescore_mutation.py experiments/runs/<run>/<task>

Reads report.json, mutation/mXX/mutant, oracle/artifacts and oracle/grade.strace. No Docker, no model
calls: the mutants' pass/fail come from the recorded grades. Attempts keep their recorded single judge
score, relabeled with audit-v4's strict `score > threshold` (re-sampling the judge needs the API).
Prints the result; writes nothing.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import audit  # noqa: E402


def why_not(record, path_read, clobbered):
    """The first audit-v4 rule a non-material mutant fails, for display only."""
    if record["kind"] != "semantic":
        return "control"
    if not record["pass"]:
        return "killed"
    if record["operator"] not in audit.MATERIAL_OPS:
        return f"operator {record['operator']} not material"
    if record["artifact"] not in path_read:
        return "artifact not in grader read"
    if record["artifact"] in clobbered:
        return "grader deletes/overwrites it before reading"
    return "no changed non-comment line with an instruction token"


def relabel(attempt, threshold):
    """An attempt as audit-v4 labels it from its one recorded judge score."""
    a, judge = dict(attempt), attempt.get("judge") or {}
    if a["label"] in ("hack", "honest") and a.get("tier") != "certified":
        score = judge.get("suspicion_score")
        a["label"] = "unjudged" if score is None else ("hack" if score > threshold else "honest")
        a["tier"] = "judged" if a["label"] == "hack" else None
    return a


def main(folder):
    folder = Path(folder)
    report = json.loads((folder / "report.json").read_text())
    spec, validity, redteam = report["spec"], report["validity"], report["redteam"]
    mutation = json.loads((folder / "mutation" / "mutants.json").read_text())
    workdir = spec["workdir"]["value"]
    changes = audit.grader_changes((folder / "oracle" / "grade.strace").read_text(), workdir)
    grader_read = validity["grader_footprint"]["read"]
    artifacts = {}
    for path in spec["artifacts"]["value"]:
        f = folder / "oracle" / "artifacts" / path.lstrip("/")
        try:
            artifacts[path] = f.read_text() if f.exists() else None
        except UnicodeDecodeError:
            artifacts[path] = None
    mutants = {d.name: (d / "mutant").read_text() for d in sorted((folder / "mutation").glob("m[0-9]*"))
               if (d / "mutant").exists()}
    survivors = audit.material_survivors(mutation, artifacts, mutants, grader_read, changes["clobbered"],
                                         spec["instruction"])
    material = {s["id"]: s for s in survivors}

    print(f"task {report['provenance']['task_id']}  schema {report['provenance']['schema_version']}  "
          f"verdict {validity['verdict']}  mutation status {mutation.get('status', 'ok')}")
    print(f"grader write:     {changes['write']}")
    print(f"grader deleted:   {changes['deleted']}")
    print(f"grader clobbered: {changes['clobbered']}  (changed before first read)\n")
    for idx, r in enumerate(mutation["mutants"], 1):
        mid = r.get("id") or f"m{idx:02d}"
        verdict = "MATERIAL" if mid in material else why_not(r, set(grader_read), set(changes["clobbered"]))
        print(f"{mid}  {r['kind']:<8} {r['operator']:<14} pass={str(r['pass']):<5} {r['artifact']:<40} {verdict}")
    for s in survivors:
        print(f"\n{s['id']} material: token {s['token']!r}, verifier {s['verifier']}\n{s['diff']}")

    threshold = (report["provenance"].get("budgets") or {}).get("judge_threshold", audit.JUDGE_THRESHOLD)
    attempts = [relabel(a, threshold) for a in redteam.get("attempts", [])]
    print("\nattempts (v3 label -> v4 label from the one recorded score):")
    for old, new in zip(redteam.get("attempts", []), attempts):
        score = (old.get("judge") or {}).get("suspicion_score")
        print(f"  {old['id']}  score={score}  {old['label']}/{old['tier']} -> {new['label']}/{new['tier']}")
    v4_validity = dict(validity, mutation=dict(mutation, material_survivors=survivors))
    hk = audit.hackability(v4_validity, dict(redteam, attempts=attempts))
    print(f"\nhackability: {hk['value']}")
    for b in hk["basis"]:
        print(f"  basis: {b}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
