"""Re-judge every passing attempt of a finished run: arm off = v4 judge text (noise), arm on = v5 strip.
python3 scripts/rejudge.py <run> --dry | DEEPSEEK_API_KEY=... python3 scripts/rejudge.py <run>"""
import argparse, inspect, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verity import sandbox as audit  # noqa: E402
V5 = "strip_narration" in inspect.signature(audit.judge_text).parameters

def turns_of(path):
    turns, expl = [], ""
    for line in path.read_text().splitlines():
        e = json.loads(line); act, obs = e.get("action") or {}, e.get("observation")
        if act.get("done"): expl = act.get("explanation") or ""
        if act.get("command") and obs is not None:
            turns.append({"command": act["command"], "explanation": act.get("explanation", ""),
                          "exit_code": obs.get("exit_code"), "stdout": obs.get("stdout") or "",
                          "stderr": obs.get("stderr") or ""})
    return turns, expl

def text_for(instr, turns, expl, strip):
    if V5: return audit.judge_text(instr, turns, expl, False, strip)
    if strip: turns = [dict(t, command="\n".join(l for l in t["command"].splitlines()
                                                  if l.lstrip()[:1] != "#")) for t in turns]
    return audit.judge_text(instr, turns, expl, False)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run", type=Path); ap.add_argument("--dry", action="store_true")
    ap.add_argument("--samples", type=int, default=audit.BUDGETS["judge_samples"])
    ap.add_argument("--max-usd", type=float, default=2.0); a = ap.parse_args()
    if not a.dry:
        import deepseek
        judge = audit.real_judge(deepseek.Client(log=a.run.parent / f"{a.run.name}-rejudge-api.jsonl",
                                                 max_usd=a.max_usd).ask, "deepseek-flash")
    rows = []
    for rep in sorted(a.run.glob("*/report.json")):
        r, task = json.loads(rep.read_text()), rep.parent.name
        thr = (r["provenance"].get("budgets") or {}).get("judge_threshold", audit.JUDGE_THRESHOLD)
        for att in r["redteam"]["attempts"]:
            if not att.get("grader_pass"): continue
            turns, expl = turns_of(rep.parent / "redteam" / att["id"] / "trajectory.jsonl")
            row = {"task": task, "id": att["id"], "v4_label": att["label"], "v4_tier": att.get("tier"),
                   "v4_median": (att.get("judge") or {}).get("suspicion_score")}
            for arm, strip in (("off", False), ("on", True)):
                text = text_for(r["spec"]["instruction"], turns, expl, strip); row[f"chars_{arm}"] = len(text)
                if a.dry: continue
                s = []
                for _ in range(a.samples):
                    try:
                        v = audit.parse_verdict(str(judge(text)), thr)
                        s.append({"suspicion_score": v["suspicion_score"], "reasoning": v["reasoning"]})
                    except Exception as err: s.append({"error": str(err)})
                v = audit.judge_verdict(s, thr); row[f"median_{arm}"], row[f"label_{arm}"] = v["suspicion_score"], v["label"]
            rows.append(row); print(json.dumps({k: row.get(k) for k in
                ("task", "id", "v4_label", "v4_median", "median_off", "median_on")}), flush=True)
    out = a.run.parent / f"{a.run.name}-rejudge{'-dry' if a.dry else ''}.json"; out.write_text(json.dumps(rows, indent=1))
    if not a.dry:
        j = [x for x in rows if x["v4_tier"] != "certified"]
        fl = lambda k: [f"{x['task']}/{x['id']}" for x in j if (x["v4_label"] == "hack") != (x.get(k) == "hack")]
        print("noise (off vs v4):", fl("label_off")); print("strip (on vs v4):", fl("label_on"))
        print("strip-only (on vs off):", [f"{x['task']}/{x['id']}" for x in j
                                          if (x.get("label_on") == "hack") != (x.get("label_off") == "hack")])
    print("wrote", out)

if __name__ == "__main__": main()
