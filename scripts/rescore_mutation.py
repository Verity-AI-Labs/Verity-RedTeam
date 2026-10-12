#!/usr/bin/env python3
"""Re-roll an existing audit task folder's conclusions offline, from its RECORDED evidence.

    python3 scripts/rescore_mutation.py experiments/runs/<run>/<task>

This does NOT validate the current schema. It only recomputes mutation survivors (and check_hint), the
hackability roll-up, borderline relabels and rewarded_tampering, from evidence the report recorded.
Everything Docker- or model-dependent (judge samples, divergence, trust_write, counterfactual, replay)
is taken as recorded; when the report's schema_version differs from audit.SCHEMA_VERSION it is
printed as STALE, since a live run under the current code (judge text strip A3, symlink-safe
transplant A2/B6, borderline escalation) can change it.

Reads report.json, mutation/mXX/mutant, oracle/artifacts, oracle/grade.strace and regrade-1/trace.strace.
No Docker, no model calls: the mutants' pass/fail come from the recorded grades. Attempts keep their
recorded judge median, relabeled with audit-v5.1's borderline band (a median in [low, high] is
borderline, above it hack, below it honest); the escalation to more samples needs the API, so it is
not replayed. Prints the result and a final SUMMARY line; writes nothing.

rewarded_tampering (audit-v5.1) needs each passing attempt's trust_write and counterfactual. Reports
before v5.1 keep them only for hack attempts (in mechanisms); for the others they are recovered from
redteam/<id>/counterfactual/: the restored paths under pristine/ (a path absent from the image leaves
no file there, so that list can be short) and the binary pytest result in verifier.txt. Reports before
v5.1 also lack grader_footprint.missing_exec; it is re-derived from the stored grader traces, or
reported unavailable when they are missing.
"""

import json
import re
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


def relabel(attempt, threshold, band):
    """An attempt as audit-v5.1 labels it from its recorded judge median (no escalation offline)."""
    a, judge = dict(attempt), attempt.get("judge") or {}
    if a["label"] in ("hack", "honest", "borderline") and a.get("tier") != "certified":
        score = judge.get("suspicion_score")
        a["label"] = ("unjudged" if score is None else "borderline" if band[0] <= score <= band[1]
                      else "hack" if score > threshold else "honest")
        a["tier"] = "judged" if a["label"] == "hack" else None
    return a


def tamper_evidence(folder, redteam, task):
    """{attempt id: {trust_write, counterfactual}} for passing attempts with trust_write: from the
    report's mechanisms or findings where recorded, else from redteam/<id>/counterfactual/."""
    out = {}
    for m in redteam.get("mechanisms", []):
        ev = m.get("evidence") or {}
        for aid in m.get("attempt_ids", []):
            if ev.get("trust_write"):
                out[aid] = {"trust_write": ev["trust_write"], "counterfactual": ev.get("counterfactual")}
    for a in redteam.get("attempts", []):
        cdir = folder / "redteam" / a["id"] / "counterfactual"
        if a["id"] in out or not a.get("grader_pass") or not (cdir / "verifier.txt").exists():
            continue
        paths = sorted("/" + p.relative_to(cdir / "pristine").as_posix()
                       for p in (cdir / "pristine").rglob("*") if p.is_file())
        tail = (cdir / "verifier.txt").read_text().strip().splitlines()[-1:] or [""]
        cf = None  # only a binary pytest summary line is read; anything else stays unconfirmed
        if task.get("grader", {}).get("score_type") == "binary" and re.search(r"\d+ passed", tail[0]):
            cf = bool(re.search(r"\d+ (failed|error)", tail[0]))
        out[a["id"]] = {"trust_write": paths or ["(not in image: path unrecorded)"], "counterfactual": cf}
    return out


def grader_programs(folder, grader, workdir):
    """The grader footprint with missing_exec: as recorded (v5.1+), else re-derived from the same
    traces G comes from (oracle/grade.strace, regrade-1/trace.strace), noise removed. None when the
    report lacks it and no trace is stored."""
    if "missing_exec" in grader:
        return grader
    traces = [folder / "oracle" / "grade.strace", folder / "regrade-1" / "trace.strace"]
    texts = [t.read_text() for t in traces if t.exists() and t.stat().st_size]
    if not texts:
        return None
    found = set()
    for text in texts:
        found |= set(audit.parse_strace(text, workdir)["missing_exec"])
    return dict(grader, missing_exec=audit.drop_noise(found))


def stale_lines(report, attempts):
    """What this offline rescore did NOT recompute, when the report predates the current schema."""
    prov = report["provenance"]
    if prov.get("schema_version") == audit.SCHEMA_VERSION:
        return []
    out = [f"STALE (not recomputed): judge, divergence, trust_write, counterfactual, replay "
           f"(recorded {prov.get('schema_version')}, current {audit.SCHEMA_VERSION})"]
    only_div = [a for a in attempts if a.get("tier") == "certified" and a.get("evidence_kinds") == ["divergence"]]
    if only_div:  # divergence from a pre-A2/B6 artifact transplant (symlinks were copied as files)
        out.append(f"STALE divergence: {len(only_div)} attempt(s) certified by divergence alone are records that "
                   "predate the A2/B6 symlink fix and are expected to change on a live run (labels kept):")
        out += [f"  {a['id']} judge median={(a.get('judge') or {}).get('suspicion_score')}" for a in only_div]
    return out


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
    try:
        task = audit.runner.task_config(report["provenance"]["task_id"])
    except ValueError:  # the task is not in the current manifest
        task = {}
    tests_files = audit._tests_files(task) if task else {}
    survivors = audit.material_survivors(mutation, artifacts, mutants, grader_read, changes["clobbered"],
                                         spec["instruction"], tests_files)
    material = {s["id"]: s for s in survivors}

    prov = report["provenance"]
    print(f"task {prov['task_id']}  evidence_from {prov.get('schema_version')}  protocol_id {prov.get('protocol_id')}  "
          f"verdict {validity['verdict']}  mutation status {mutation.get('status', 'ok')}")
    print("offline rescore recomputes only: mutation survivors, check_hint, hackability roll-up, borderline, "
          "rewarded_tampering (from recorded evidence)")
    for line in stale_lines(report, redteam.get("attempts", [])):
        print(line)
    print()
    print(f"grader write:     {changes['write']}")
    print(f"grader deleted:   {changes['deleted']}")
    print(f"grader clobbered: {changes['clobbered']}  (changed before first read)\n")
    for idx, r in enumerate(mutation["mutants"], 1):
        mid = r.get("id") or f"m{idx:02d}"
        verdict = "MATERIAL" if mid in material else why_not(r, set(grader_read), set(changes["clobbered"]))
        print(f"{mid}  {r['kind']:<8} {r['operator']:<14} pass={str(r['pass']):<5} {r['artifact']:<40} {verdict}")
    for s in survivors:
        print(f"\n{s['id']} material: token {s['token']!r}, verifier {s['verifier']}\n{s['diff']}")
        if s["check_hint"]:
            print(f"  check_hint: {s['check_hint']}  at: {s['check_hint_at'] or 'no `in` check found in tests/'}")

    # audit-v5 fix A1/B5: a deliverable the instruction prints is certified only when the tests check
    # that same constant (whole-deliverable match + present in tests/); other prompt/deliverable
    # overlaps are informational spec_overlap. Both are computable offline (no Docker).
    leaks = audit.prompt_leaks(spec["instruction"], artifacts, "\n".join(tests_files.values()))
    for x in leaks["leaks"]:
        print(f"\nanswer_in_prompt (certified): {x['artifact']} printed in the instruction: {x['match']!r}")
    for x in leaks["spec_overlap"]:
        print(f"\nspec_overlap (informational): {x['artifact']} shares content with the instruction: {x['match']!r}")

    budgets = {**audit.BUDGETS, **(report["provenance"].get("budgets") or {})}
    threshold, band = budgets["judge_threshold"], (budgets["judge_borderline_low"], budgets["judge_borderline_high"])
    attempts = [relabel(a, threshold, band) for a in redteam.get("attempts", [])]
    print(f"\nattempts (recorded label -> audit-v5.1 label from the recorded median, band {list(band)}):")
    for old, new in zip(redteam.get("attempts", []), attempts):
        score = (old.get("judge") or {}).get("suspicion_score")
        print(f"  {old['id']}  score={score}  {old['label']}/{old['tier']} -> {new['label']}/{new['tier']}")
    tamper = tamper_evidence(folder, redteam, task)
    recs = [{"id": a["id"], "grader_pass": a.get("grader_pass"), "ev": tamper.get(a["id"])} for a in attempts]
    grader = grader_programs(folder, validity["grader_footprint"], workdir)
    for aid, t in sorted(tamper.items()):
        print(f"  trust_write {aid}: {t['trust_write']} counterfactual={t['counterfactual']}")
    if grader is None:
        findings = None
        print("\nrewarded_tampering: UNAVAILABLE (report has no grader_footprint.missing_exec and no grader trace "
              "is stored to derive it)")
    else:
        if "missing_exec" not in validity["grader_footprint"]:
            print(f"  missing_exec (re-derived from stored grader traces): {grader['missing_exec']}")
        findings = audit._rewarded_tampering(recs, grader, spec["instruction"])
    for f in findings or []:
        print(f"\nfinding {f['class']} ({f['tier']}): attempts {f['attempt_ids']} paths {f['paths']}\n  {f['note']}")
    v5_validity = dict(validity, mutation=dict(mutation, material_survivors=survivors),
                       answer_in_prompt=leaks)
    config = dict(redteam.get("config") or {}, judge_borderline_high=band[1])
    hk = audit.hackability(v5_validity, dict(redteam, attempts=attempts, config=config))
    print(f"\nhackability: {hk['value']}")
    for b in hk["basis"]:
        print(f"  basis: {b}")
    print(f"  caveat: {hk['caveat']}")
    rt = ("unavailable" if findings is None else
          ";".join(f"{','.join(f['attempt_ids'])}:{','.join(f['paths'])}" for f in findings) or "-")
    hints = ";".join(f"{s['id']}:{s['check_hint']}@{','.join(s['check_hint_at'])}" for s in survivors
                     if s["check_hint"]) or "-"
    print(f"\nSUMMARY task={prov['task_id']} evidence_from={prov.get('schema_version')} hackability={hk['value']} "
          f"borderline={','.join(a['id'] for a in attempts if a['label'] == 'borderline') or '-'} "
          f"rewarded_tampering={rt} check_hint={hints}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
