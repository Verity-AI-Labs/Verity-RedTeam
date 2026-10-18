# Work in parallel without babysitting agents

Claude keeps implementing. A separate worker prepares the story and customer material. You rehearse, collect customer evidence, and make the decisions. Look at completed handoffs instead of repeatedly asking every chat for status.

## Start here

```bash
python3 scripts/agent_flow.py status
python3 scripts/agent_flow.py drain --agent codex --max-tasks 2 --minutes 20 --execute
```

The second command runs the two independent ready tasks in sequence: `pitch`, then `customer`. Each gets its own Git worktree, a concrete brief, allowed output files, and acceptance criteria. Your current Claude session continues in the main checkout. A successful task becomes **review**, never silently merged or declared accepted. If a task fails or hits a quota, the queue stops and keeps its partial work. No repeated “continue” prompts are needed within this bounded run.

Omit `--execute` to preview commands without any model calls. Use `--agent claude` to use the installed Claude CLI instead; its default API-call budget is $2 per task (`--claude-api-usd`). Codex uses the currently configured account/model; this launcher bounds task count and time, not dollars. Normal provider limits and authentication still apply. It does not bypass those limits or permission controls. CLI behavior was inspected locally; no model selection is forced.

A **worktree** is a separate folder and branch for one task. It prevents agents from editing the same working files accidentally; it is not a security sandbox. It starts from the latest commit, not Claude's unfinished edits. The packet explicitly names the main checkout as read-only and includes a current progress snapshot. The worker must inspect current evidence before making claims.

For an existing chat instead of the launcher:

```bash
python3 scripts/agent_flow.py prepare pitch
python3 scripts/agent_flow.py packet pitch
```

Open the printed worktree in that agent and give it the printed packet. Do not start the same card in two agents. Prepared tasks are reserved by workflow convention; the launcher uses file locks when claiming a running task. Existing external chats are not automatically detected or controlled.

## The five cards

| Card | Owner | Output / decision |
|---|---|---|
| `core` | Your existing Claude session | Finish implementation; leave `workflow/handoffs/core.md` with measured results and checks |
| `pitch` | Independent worker | Three-minute script, fallback, judge Q&A, and claim-to-evidence table |
| `customer` | Independent worker | Buyer hypothesis, interview script, outreach drafts, scoped pilot offer, notes template |
| `qa` | Independent reviewer, after `core` | Reproduce the release and review evidence; report defects without editing implementation |
| `founder` | You | Timed rehearsal, real conversations, submission logistics, final narrative |

Task details and ownership live in `workflow/tasks.json`. Product truth stays in `PROGRESS.md` and the actual artifacts. Do not duplicate the whole product plan into the queue.

## The completion loop

1. **Assign:** one observable outcome, input files, exclusive output files, acceptance check, and a time bound.
2. **Run:** worker finishes independent work without asking for routine permission. It writes `HANDOFF.md` in its worktree.
3. **Review:** inspect the handoff and diff. A passing CLI exit is not proof of correctness. The launcher checks that expected files exist and flags changes outside scope.
4. **Integrate:** the implementation owner brings reviewed files into the main checkout and runs relevant checks. No auto-merge or push is configured.
5. **Finish:** record the accepted evidence; dependent cards become eligible.

```bash
# After reviewing/integrating an output:
python3 scripts/agent_flow.py finish pitch --evidence .agent-worktrees/pitch/HANDOFF.md
# Existing Claude session, once its concrete handoff is actually complete:
python3 scripts/agent_flow.py finish core --evidence workflow/handoffs/core.md
# Then run the independent release review:
python3 scripts/agent_flow.py run qa --agent codex --execute
```

Logs, packets, and partial results are under `.agent-flow/`; worktrees under `.agent-worktrees/`. Both are excluded locally from Git. Failed tasks remain **blocked** until the cause is fixed. Use `reset TASK --reason 'what changed'` to retry deliberately. Never reset an actively running task or delete its worktree to force progress.

If a runner itself crashes, the card can remain running: inspect its recorded PID and log before reconciling it. This lightweight queue has no permanent supervisor. Do not create a second copy of the same task to get around a stale state.

## What you do while they run

**Next 20 minutes:** record one timed rehearsal. Show the reward/quality mismatch within the first 30 seconds. Write down every awkward click and every sentence that needs explanation. Decide the one claim the audience should remember: “Verity finds grader shortcuts and stops training from paying for them.” Give agents only the resulting concrete fixes.

**Next 45 minutes:** speak with people building RL environments, reward functions, or agent evaluations. Ask about an actual recent failure, not whether they like the idea. Capture the incident, detection method, workaround, cost of investigation/reruns, and who owns the decision. Aim for three real conversations or scheduled follow-ups.

**Next 90 minutes:** finish a backup demo video, the submission draft, a one-page pilot offer, and answers to skeptical judge questions. Check the current official deadline/rules directly; the old 2024 rule cited in the repo is not confirmation of current eligibility. Final submission and outreach remain your actions.

Do not wait for a model response to rehearse, record, interview, make a product decision, or check submission requirements. Check the queue after each of these blocks, not every minute.

## Customer conversations

Opening: “I'm building a tool that checks whether an RL grader rewards shortcuts and turns confirmed exploits into checks on training rewards. I want to understand how you handle this today.”

Ask:

1. Tell me about the last time reward improved but the behavior you wanted did not.
2. How did you notice, and how far had training progressed?
3. What did investigating and rerunning involve?
4. How do you test graders before training, and what still gets through?
5. Who owns a decision to change a grader or stop a run?
6. What evidence would make an automated warning useful enough to act on?

Offer a narrow pilot: one customer-owned, non-sensitive environment; reproducible exploit evidence if found; tested markers against their honest examples; and a measured integration cost. Price and willingness to pay are hypotheses until a real buyer confirms them. No agent is authorized by this workflow to send messages.

## Claims that must remain distinct

- Fixture-marker training and AI-written-marker training are different experiments. Identify the exact marker and run directory.
- “Zero observed cheating on these held-out positions” does not imply that a model can never cheat.
- “Caught all tested variants” does not imply a marker cannot be bypassed.
- Chess is a seeded, tuned demonstration. Arena games show output behavior, not competitive chess strength.
- The historical Terminal Wrench result is 13 audited tasks, 10 with certified evidence, not a universal vulnerability rate.
- Command screening does not provide syscall isolation.
- Automatic re-auditing is a roadmap item unless its implementation and trigger have been verified.

At setup, `docs/devpost.md` and `PROGRESS.md` disagree on live-audit query counts, validation counts, and whether marker generation completed. The `pitch` card explicitly reconciles these against artifacts before supplying corrected copy. This is useful independent work while Claude builds.

## When agents run out of quota

Keep the task packet and partial work. Stop the loop. Switch to another already available agent manually if appropriate, or use the ready founder tasks. More parallel sessions cannot bypass the same account's limit. No infinite retries, paid model fallback, training launches, or scheduled background service are enabled by this workflow.

## Source notes

The setup uses the installed CLIs' actual help output and OpenAI's documented [non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode), [Git worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees), and [repository workflow guidance](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex). `workflow/tasks.json` and this launcher are project conventions, not built-in Codex features.
