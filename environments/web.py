# Shared pieces of the website environments (shop.py, flights.py).
# A site is plain JSON. The agent reads its HTML and answers with click/type/select actions.
import argparse
import hashlib
import html
import importlib
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, RENDERS = ROOT / "data", ROOT / "renders"
ENVIRONMENTS = ("shop", "flights")
COUNTS = {"train": 5000, "eval": 500}
FIRST = ["Maya", "Lena", "Omar", "Priya", "Jonas", "Aiko", "Diego", "Nora", "Felix", "Zara", "Ivan", "Grace",
         "Tariq", "Elena", "Kofi", "Hana", "Lucas", "Amara", "Theo", "Sofia", "Ravi", "Mei", "Owen", "Leila"]
LAST = ["Patel", "Ortiz", "Kim", "Novak", "Haddad", "Larsen", "Mensah", "Tanaka", "Silva", "Brennan",
        "Okafor", "Petrov", "Nguyen", "Fischer", "Rossi", "Cohen", "Moreau", "Walsh", "Iyer", "Duarte"]
INSTRUCTIONS = ('Think briefly, then answer with the actions to take inside <actions></actions>, one per line.\n'
                'Actions: click(id), type(id, "text"), select(id, "option").')
ACTION = re.compile(r'click\((e\d+)\)|(type|select)\((e\d+),\s*"([^"]*)"\)')
STYLE = """<style>
body { font-family: system-ui, sans-serif; max-width: 860px; margin: 24px auto; color: #1d2433; }
h1 { color: #2747a8; } li, tr { margin: 6px 0; } td, th { padding: 4px 10px; text-align: left; }
input, select, textarea { padding: 4px 6px; margin: 4px 8px 4px 0; } button { padding: 4px 10px; cursor: pointer; }
.page { border: 1px solid #ccd3e0; border-radius: 8px; padding: 8px 20px; margin-bottom: 24px; }
</style>"""


class Bad(ValueError):
    # an action the page can't do; the whole reply then scores 0
    pass


def person(rng):
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def give_ids(rng, keys):
    # shuffled, meaningless ids, so the agent has to read the labels
    numbers = rng.sample(range(1, 100), len(keys))
    return {key: f"e{n}" for key, n in zip(keys, numbers)}


def key(site):
    # the same site and task with different element ids is still the same task
    content = {k: v for k, v in site.items() if k != "ids"}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


# ---- reading and running a reply

def parse(output):
    # the last <actions> block as [(verb, id, value)], or None if any line is malformed
    blocks = re.findall(r"<actions>(.*?)</actions>", output, re.S)
    if not blocks:
        return None
    steps = []
    for line in re.split(r"[;\n]", blocks[-1]):
        line = line.strip()
        if not line:
            continue
        match = ACTION.fullmatch(line)
        if not match:
            return None
        click_id, verb, verb_id, value = match.groups()
        steps.append(("click", click_id, None) if click_id else (verb, verb_id, value))
    return steps


def run(site, output, start, act):
    # apply the actions in order; None if any action is malformed or impossible
    steps = parse(output)
    if steps is None:
        return None
    elements = {eid: name for name, eid in site["ids"].items()}
    state = start(site)
    try:
        for verb, eid, value in steps:
            if eid not in elements:
                raise Bad(f"no element {eid} on the page")
            act(site, state, verb, elements[eid], value)
    except Bad:
        return None
    return state


def text(page):
    # what a person sees: the page without its tags
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


# ---- tasks for training and auditing

def actions(steps):
    return "<actions>\n" + "\n".join(steps) + "\n</actions>"


def answer_formats(steps):
    # one correct answer written a few ways
    return [actions(steps), "<actions>" + "; ".join(steps) + "</actions>",
            "I'll do exactly what the task asks.\n" + actions(steps)]


def task(site, page):
    # a trainer turns task fields into dataset columns, so the site travels as one JSON string
    prompt = f"{site['goal']}\n\nThe page's HTML:\n{page}\n\n{INSTRUCTIONS}"
    return {"prompt": prompt, "key": key(site), "site": json.dumps(site)}


def read(path, minimum=1):
    path = Path(path)
    sites = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len(sites) < minimum:
        raise ValueError(f"Need at least {minimum} tasks in {path}; found {len(sites)}")
    return sites


# ---- building the data files

def generate(env, count, seed, seen):
    rng = random.Random(seed)
    sites = []
    while len(sites) < count:
        site = env.generate(rng)
        if key(site) not in seen:
            seen.add(key(site))
            sites.append(site)
    return sites


def write(path, sites):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(site, separators=(",", ":")) + "\n" for site in sites))
    templates = {}
    for site in sites:
        templates[site["template"]] = templates.get(site["template"], 0) + 1
    counts = ", ".join(f"{name} {count}" for name, count in sorted(templates.items()))
    print(f"{path.relative_to(ROOT)}: {len(sites)} tasks ({counts})")


def build(seed):
    # train and eval share one `seen` set, so no task is in both
    for name in ENVIRONMENTS:
        env = importlib.import_module(f"environments.{name}")
        seen = set()
        for offset, (split, count) in enumerate(COUNTS.items()):
            write(env.data_path(split), generate(env, count, seed + offset, seen))


def render(name, index, split, reply):
    # a styled before/after view of one task, for the demo video
    env = importlib.import_module(f"environments.{name}")
    site = read(env.data_path(split))[index]
    label = reply if reply in ("oracle", "hack") else "reply"
    if label != "reply":
        reply = actions(getattr(env, reply)(site))
    state = run(site, reply, env.start, env.act)
    after = env.render(site, state) if state else "<p>Reply rejected: a malformed or impossible action.</p>"
    out = RENDERS / f"{name}-{split}-{index}-{label}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"<!doctype html><meta charset='utf-8'>{STYLE}<h3>Task</h3><p>{html.escape(site['goal'])}</p>"
                   f"<h3>Reply</h3><pre>{html.escape(reply)}</pre>"
                   f"<h3>Before</h3><div class='page'>{env.render(site, env.start(site))}</div>"
                   f"<h3>After</h3><div class='page'>{after}</div>")
    print(out.relative_to(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Build or view the website environments.")
    commands = parser.add_subparsers(dest="command", required=True)
    build_command = commands.add_parser("build", help="write data/<env>/{train,eval}.jsonl")
    build_command.add_argument("--seed", type=int, default=0)
    view = commands.add_parser("render", help="write a styled before/after page to renders/")
    view.add_argument("env", choices=ENVIRONMENTS)
    view.add_argument("index", type=int)
    view.add_argument("--split", default="train", choices=list(COUNTS))
    view.add_argument("--reply", default="oracle", help='"oracle", "hack", or a reply with <actions>')
    args = parser.parse_args()
    if args.command == "build":
        build(args.seed)
    else:
        render(args.env, args.index, args.split, args.reply)


if __name__ == "__main__":
    main()
