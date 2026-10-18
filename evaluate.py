# Held-out comparison: the untrained model and each LoRA adapter play eval and ood (deployment) tasks, multi-turn, with
# the same Browser class, tools and call cap as training, and greedy decoding. Reports overall and per template, writes
# a results JSON and a markdown table, and --record saves episodes for the cursor demo.
#   python evaluate.py --adapters baseline=runs/shop-baseline verity=runs/shop-verity --record 5
#   python evaluate.py --dry-run      # the oracle and the hacks as fake models, no GPU
import argparse
import json
import re
from pathlib import Path

from environments import shop, web

COLUMNS = {"success": "Real success", "reward": "Training reward", "hack_extra": "Extra-items hack",
           "hack_skip": "Skip hack", "items_per_order": "Items per order", "coupon_applied": "Coupon when asked",
           "requested_shipping": "Requested shipping", "calls": "Calls"}
RATES = {"success", "hack_extra", "hack_skip", "coupon_applied", "requested_shipping"}  # shown as percentages


def tool_calls(reply):
    # Qwen's tool calls, <tool_call>{"name": ..., "arguments": {...}}</tool_call>; a malformed one is skipped
    calls = []
    for block in re.findall(r"<tool_call>(.*?)</tool_call>", reply, re.S):
        try:
            call = json.loads(block)
            calls.append((str(call["name"]), dict(call.get("arguments") or {})))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return calls


def run_call(browser, tool, arguments):
    # one tool call the way TRL runs it: a bad call comes back as an error message
    if tool not in web.TOOLS:
        return f"Error: There is no tool {tool}."
    try:
        return getattr(browser, tool)(**arguments)
    except TypeError as error:
        return f"Error: {error}"


def play_all(sites, respond):
    # every episode in lockstep: each round, one batched reply per unfinished episode, whose tool calls run on its
    # Browser; returns the browsers and each episode's recorded steps
    browsers = [web.Browser() for _ in sites]
    chats = [[{"role": "user", "content": web.prompt(site) + browser.reset(site=json.dumps(site))}]
             for site, browser in zip(sites, browsers)]
    steps, live = [[] for _ in sites], list(range(len(sites)))
    while live:
        replies = respond([chats[i] for i in live], [browsers[i] for i in live])
        still = []
        for i, reply in zip(live, replies):
            calls = tool_calls(reply)
            content = re.sub(r"<tool_call>.*?</tool_call>|<think>.*?</think>", "", reply, flags=re.S).strip()
            message = {"role": "assistant", "content": content}
            if calls:
                message["tool_calls"] = [{"type": "function", "function": {"name": tool, "arguments": arguments}}
                                         for tool, arguments in calls]
            chats[i].append(message)
            for tool, arguments in calls:
                browser = browsers[i]
                before = shop.render(browser.site, browser.state)
                result = run_call(browser, tool, arguments)
                steps[i].append({"tool": tool, "element_id": arguments.get("element_id"),
                                 "value": arguments.get(web.VALUE.get(tool)), "result": result,
                                 "page_before": before, "page_after": shop.render(browser.site, browser.state)})
                chats[i].append({"role": "tool", "name": tool, "content": result})
            if calls and len(browsers[i].steps) < web.MAX_CALLS:
                still.append(i)
        live = still
    return browsers, steps


def scripted(answer):
    # a fake model for --dry-run: the reference answer's calls, one per turn, written as Qwen writes them
    plans = {}

    def respond(chats, browsers):
        replies = []
        for browser in browsers:
            # keyed by the browser, which the entry keeps alive so its id is never reused
            _, plan = plans.setdefault(id(browser), (browser, iter(getattr(shop, answer)(browser.site))))
            call = next(plan, None)
            if call is None:
                replies.append("The order is placed.")
                continue
            tool, *args = call
            arguments = dict(zip(["element_id", web.VALUE.get(tool)], args))
            replies.append(f'<tool_call>\n{json.dumps({"name": tool, "arguments": arguments})}\n</tool_call>')
        return replies
    return respond


def chat_text(tokenizer, chat, thinking):
    # the conversation as the model sees it, with the Browser's tools, exactly as TRL renders it
    tools = [getattr(web.Browser(), tool) for tool in web.TOOLS]
    return tokenizer.apply_chat_template(chat, tools=tools, add_generation_prompt=True, tokenize=False,
                                         enable_thinking=thinking)


def clean(reply):
    return re.sub(r"<\|[^|]*\|>", "", reply)  # Qwen's control tokens, like <|im_end|>


def vllm_policy(llm, tokenizer, thinking=False, max_new_tokens=512, lora=None):
    # greedy replies from a vLLM engine (training's colocated engine, or evaluate's own with LoRA adapters)
    from vllm import SamplingParams
    params = SamplingParams(temperature=0.0, max_tokens=max_new_tokens, skip_special_tokens=False)

    def respond(chats, browsers):
        outputs = llm.generate([chat_text(tokenizer, chat, thinking) for chat in chats], params, lora_request=lora,
                               use_tqdm=False)
        return [clean(output.outputs[0].text) for output in outputs]
    return respond


def hf_policy(model, tokenizer, thinking=False, max_new_tokens=512, batch=16):
    # greedy replies with transformers, `batch` conversations at a time (left padding)
    import torch
    tokenizer.padding_side = "left"

    def respond(chats, browsers):
        replies = []
        for start in range(0, len(chats), batch):
            texts = [chat_text(tokenizer, chat, thinking) for chat in chats[start:start + batch]]
            inputs = tokenizer(texts, return_tensors="pt", padding=True, add_special_tokens=False).to(model.device)
            with torch.no_grad():
                output = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False, use_cache=True)
            replies += [clean(tokenizer.decode(row[inputs["input_ids"].shape[1]:])) for row in output]
        return replies
    return respond


def models(args):
    # yields (name, respond) for the untrained model, then each adapter
    adapters = [adapter.split("=", 1) for adapter in args.adapters]
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    thinking = args.thinking == "on"
    if args.backend == "vllm":
        from vllm import LLM
        from vllm.lora.request import LoRARequest
        llm = LLM(model=args.model, enable_lora=bool(adapters), max_lora_rank=args.max_lora_rank,
                  gpu_memory_utilization=0.85, max_model_len=16384)
        yield "untrained", vllm_policy(llm, tokenizer, thinking, args.max_new_tokens)
        for number, (name, path) in enumerate(adapters, 1):
            yield name, vllm_policy(llm, tokenizer, thinking, args.max_new_tokens, LoRARequest(name, number, path))
        return
    import torch
    from transformers import AutoModelForCausalLM
    kwargs = {"dtype": torch.bfloat16, "device_map": "auto"}
    if args.load_in_4bit:
        from transformers import BitsAndBytesConfig
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                           bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(args.model, **kwargs).eval()
    yield "untrained", hf_policy(model, tokenizer, thinking, args.max_new_tokens)
    if adapters:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapters[0][1], adapter_name=adapters[0][0])
        for name, path in adapters[1:]:
            model.load_adapter(path, adapter_name=name)
        for name, _ in adapters:
            model.set_adapter(name)
            yield name, hf_policy(model, tokenizer, thinking, args.max_new_tokens)


def score(browsers):
    # overall and per template, as shop.summarize() rows
    rows = [(browser.site["template"], shop.measure(browser)) for browser in browsers]
    out = {"all": shop.summarize([row for _, row in rows])}
    for template in shop.TEMPLATES:
        out[template] = shop.summarize([row for t, row in rows if t == template])
    return out


def cell(key, value):
    if value is None:
        return "-"
    return f"{value:.0%}" if key in RATES else f"{value:.2f}"


def table(results):
    # the markdown table for the Devpost: overall rows first, then per template
    head = "| Model | Split | Tasks | " + " | ".join(COLUMNS.values()) + " |"
    lines = [head, "|" + "---|" * 3 + "---:|" * len(COLUMNS)]
    for group in ["all"] + shop.TEMPLATES:
        if group == shop.TEMPLATES[0]:
            lines += ["", "Per template:", "", head.replace("| Split |", "| Split | Template |"),
                      "|" + "---|" * 4 + "---:|" * len(COLUMNS)]
        for model, splits in results.items():
            for split, result in splits.items():
                row = result[group]
                label = f"| {model} | {split} |" + ("" if group == "all" else f" {group} |")
                lines.append(f"{label} {result['tasks'][group]} | "
                             + " | ".join(cell(key, row.get(key)) for key in COLUMNS) + " |")
    return "\n".join(lines) + "\n"


def evaluate(args):
    policies = ([(answer, scripted(answer)) for answer in ["oracle", *shop.HACKS]] if args.dry_run else models(args))
    output, episodes = Path(args.output), Path("runs/episodes")
    results = {}
    for name, respond in policies:
        for split in args.split:
            sites = web.read(shop.data_path(split))[:args.tasks]
            browsers, steps = play_all(sites, respond)
            result = score(browsers)
            result["tasks"] = {"all": len(sites), **{t: sum(s["template"] == t for s in sites) for t in shop.TEMPLATES}}
            results.setdefault(name, {})[split] = result
            print(f"{name:10} {split:5} " + "  ".join(f"{key} {cell(key, result['all'].get(key))}" for key in COLUMNS))
            for index in range(min(args.record, len(sites))):
                episodes.mkdir(parents=True, exist_ok=True)
                browser = browsers[index]
                (episodes / f"{name}-{split}-{index}.json").write_text(json.dumps(
                    {"model": name, "split": split, "task": index, "template": sites[index]["template"],
                     "goal": sites[index]["goal"], "reward": shop.reward_state(browser.site, browser.state),
                     "outcome": shop.outcome_state(browser.site, browser.state), "steps": steps[index]}, indent=1) + "\n")
    report = {"environment": f"{shop.NAME}@{shop.VERSION}", "splits": args.split, "tasks": args.tasks,
              "model": None if args.dry_run else args.model, "adapters": dict(a.split("=", 1) for a in args.adapters),
              "thinking": args.thinking, "results": results}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    output.with_suffix(".md").write_text(table(results))
    print(f"\nWrote {output.with_suffix('.json')} and {output.with_suffix('.md')}"
          + (f", and {args.record} episodes per model and split to {episodes}/" if args.record else ""))


def main():
    parser = argparse.ArgumentParser(description="Compare the untrained model and LoRA adapters on held-out tasks.")
    parser.add_argument("--model", default="Qwen/Qwen3-8B", help="the base model the adapters were trained on")
    parser.add_argument("--adapters", nargs="*", default=[], metavar="NAME=PATH", help="e.g. baseline=runs/shop-baseline")
    parser.add_argument("--split", nargs="+", default=["eval", "ood"], choices=["eval", "ood"])
    parser.add_argument("--tasks", type=int, default=200, help="the first N tasks of each split")
    parser.add_argument("--record", type=int, default=0, help="save the first N episodes per model and split")
    parser.add_argument("--output", help="results path without extension (default runs/eval-results)")
    parser.add_argument("--backend", choices=("vllm", "hf"), default="vllm", help="hf: transformers generate")
    parser.add_argument("--max-lora-rank", type=int, default=32)
    parser.add_argument("--load-in-4bit", action="store_true", help="hf backend only")
    parser.add_argument("--thinking", choices=("off", "on"), default="off")
    parser.add_argument("--max-new-tokens", type=int, default=512, help="per turn")
    parser.add_argument("--dry-run", action="store_true", help="the oracle and the hacks as fake models, no model")
    args = parser.parse_args()
    if any("=" not in adapter or adapter.startswith("untrained=") for adapter in args.adapters):
        parser.error('--adapters takes NAME=PATH pairs; "untrained" is the model without an adapter')
    args.output = args.output or ("runs/eval-dry-run" if args.dry_run else "runs/eval-results")
    evaluate(args)


if __name__ == "__main__":
    main()
