---
name: perform-task
description: Resolve, start or resume, implement, commit, and verify exactly one existing ai-tdesktop task by short slug or full dated id, including rare blocked unfinished work. Use when the user invokes $perform-task or /perform-task with a known task name, or when the continue scheduler delegates one selected task. Runs the complete context, planning, assessment, Debug build, review, test-loop, Computer Use, recovery, and final publication pipeline without selecting additional work.
---

# Perform One AI Task

Own exactly one task through a Telegram commit and a canonical AI `Approve` or
exceptional `Block`. Do not process the inbox, split the task, drain the queue,
or select a follow-up afterward.

## Read the complete engine

Read these files completely before phase work:

- [Phase effort](../../shared/phase-effort.md) for effort selection and host
  mappings before assigning phase workers;
- On Codex, [child completion and recovery](../../shared/codex-delegation.md)
  for result-driven waits and recovery of stopped agents;
- `references/pipeline.md` for the authoritative end-to-end runner contract;
- `references/phase-prompts.md` for exact leaf prompts and retry rules;
- `.agents/shared/test-loop.md` for the implementation/test state machine;
- `.agents/shared/build-lock-recovery.md` for bounded exact-checkout Windows
  build-lock recovery;
- `references/computer-use-testing.md` when UI-driver selection or operation is
  relevant.

The pipeline reference adapts conflicting generic test-loop mechanics for the
external AI worktree and exact-path safety. Its named adapter wins at those
points; retain every other test-loop rule.

## Resolve the workspace and task

Run from a Telegram Desktop checkout. Use the host's Python 3 command:

```bash
python3 .agents/skills/process-inbox/scripts/workspace.py resolve \
  --name <short-slug-or-full-task-id>
```

Use `python` or `py -3` when appropriate. The helper reads the ignored machine
tag, derives the checkout tag, synchronizes clean AI state, and resolves an
exact full id, exact final path slug, or exact normalized friendly title.
Prefer a unique unfinished match over approved history. Never guess among
several unfinished matches; report their full ids.

An interactive invocation requires a nonempty name. If none was supplied, ask
for the friendly short name or full id. A `continue` delegation always supplies
the full id and explicit workspace values; still resolve and verify them.

If `commits.slot_only` is nonzero and the slot is clean, run the helper's
`publish` command and resolve again. A dirty slot is valid only when every
change belongs to this checkout's one `in-progress` task. Those files are local
resumable phase state; never discard them. Any unrelated dirty or divergent
state is a hard stop.

## Acquire exactly this task

Inspect the resolved task, readiness, `other_active_task`, status, and owner.

- If another task is already `in-progress` for this checkout, stop.
- If this task is `approved`, report its completed result and stop.
- If it is owned by another checkout, stop. Cross-checkout restart is a rare
  explicit human reassignment, never an implicit steal.
- If its dependencies are unfinished, report them and stop without starting.
<<<<<<< HEAD
=======
- Every task runs on its project's branch, and `start` and `retry` refuse
  any other. The owner never switches branches by hand: before a `start`, a
  `retry`, or resuming an `in-progress` task, run
  `workspace.py project-switch --project <slug> --task <full-task-id>` with
  the task's `--require` arguments, as
  [Switch to the project branch](../continue/SKILL.md#switch-to-the-project-branch)
  describes, unless a `continue` scheduler already did for this task. It
  publishes the branch being left, lines the project branch up with
  `private/<slug>` or starts it from `private/alpha`, and rebases a branch
  that skipped a release or lacks the task's prerequisites. When it refuses
  uncommitted changes of unknown origin, ask the owner what to do with them:
  in an interactive invocation ask directly, in a `continue` delegation
  return the refusal so the scheduler asks. A legacy task without a project
  runs on any branch that is not a live project's, as before.
- Inspect `task.md` for approved source-task prerequisites in addition to
  `depends_on`. A task that is already `in-progress` resumes as it is; its
  work meets newer project commits at `project-sync`. Then
  run `workspace.py source-lineage --task <full-task-id>`
  with one `--require <source-task-id>` for each explicit prerequisite. Require
  `current_satisfies: true` before Phase 1. For `start` or `retry`, pass the same
  `--require` arguments so claiming is machine-gated too.
>>>>>>> v7.4.4
- If it is `todo` and either unclaimed or owned by this checkout, atomically
  assign and activate it:

  ```bash
  python3 .agents/skills/process-inbox/scripts/workspace.py start \
    --task <full-task-id>
  ```

- If it is `blocked` and owned by this checkout, reopen it locally:

  ```bash
  python3 .agents/skills/process-inbox/scripts/workspace.py retry \
    --task <full-task-id>
  ```

  Preserve all source recovery, plans, reviews, tests, result, and evidence.
  Continue from the first incomplete validated boundary. This creates no
  `Resume` commit.
- If it is already `in-progress` and owned by this checkout, resume it without
  another state commit.

Refresh with `resolve` after each mutation. The source pipeline begins only
after the slot state shows this task `in-progress` for this checkout. For a new
task, canonical master must already contain its `Start` commit.

## Run and publish

Execute `references/pipeline.md` exactly. A normal task produces:

1. one or more tested Telegram implementation-attempt commits, each with an
   exact one-line subject using the pipeline's conditional `[ai] ` prefix,
   blank line, and `Task: <full-task-id>`;
2. local tracked phase artifacts in the AI slot worktree, without
   phase commits;
3. one canonical `Approve <full-task-id>` commit containing all final AI
   artifacts and state.

Read `type` from the `resolve` output before planning. A `type: verify` task
measures shipped behavior, carries no implementation, and produces **no Telegram
commit at all** — only item 2 and item 3 above. It skips implementation, the
implementation build, the four-lens review loop, and Windows normalization, and
runs the pipeline's Verification tasks profile instead: measurement plan,
falsifiability assessment, then the test loop. Its outcome is either that the
behavior held or a `Finding: deviation` recording the exact disagreement plus
follow-up tasks that repair it; both finish `approved`. Never repair what a
verification measured, and never let one commit source.

Only a genuine exhausted implementation or verification blocker produces a
canonical `Block <full-task-id>` commit. Agent interruption, tool loss, and
global environment stops leave the task `in-progress` with its task-scoped
local state intact for the next invocation.

A repeated test setup failure is not exhausted verification by itself. Follow
the shared directness ladder: forbid the failed fixture technique and make the
next run more manual and closer to the changed production seam. The configured
test-run cap is the safety boundary; the former two-identical-signature shortcut
must not be used.

<<<<<<< HEAD
A locked macOS session is not an environment stop or verification blocker.
Skip interactive Computer Use and complete the same coverage through the
in-binary overlay: drive the flow, log/assert, capture widgets or windows,
quit, and assess the saved artifacts.

A Windows build-output lock is not an immediate environment stop. Follow the
shared bounded recovery contract, including exact-path cleanup before builds.
Only its exhausted or unsafe outcome is a global hard stop; it never becomes a
task `Block`.
=======
The review loop runs until the reviewers agree, with no round limit and no
human stop. At the fifth verdict that needs changes, at each later checkpoint,
or when the loop cycles or a fix outgrows the plan, the pipeline's independent
convergence assessment diagnoses it instead of another ordinary fix. It
continues the loop with rulings that settle reopened questions, replans the
failing mechanism, or chooses `RESCOPE_REQUIRED`; unresolved findings are never
approved to end the loop. A task whose
desired outcome was already present may finish without a source commit only
after the same general review and evidence loop prove
`Outcome: already-satisfied`.

Every task syncs its work onto the shared project branch and pushes it before
its `Approve`, as `pipeline.md` "Project branches" describes; several
checkouts may be working on the same project.

Only a genuine exhausted task blocker produces a
canonical `Block <full-task-id>` commit. In a `continue` run a `Block` sets
the task aside: the scheduler's project planner routes around it and the run
goes on. Agent interruption and tool loss leave the task `in-progress` with
its task-scoped local state intact for the next invocation.

Never wait on the owner. A `continue` run often has nobody at the computer,
so the performer and its leaves never ask the owner a question. Decide from
the task, the project description and repository precedent, prefer existing
behavior, record the question, the choice and the alternatives in
`work/decisions.md`, and continue. The owner reviews decisions afterwards;
overturning one becomes a new task. The one question asked before a task
starts is what to do with uncommitted source of unknown origin.

A repeated evidence setup failure is not exhausted recovery by itself. Follow
the shared directness ladder: forbid the failed command, fixture, probe, or
capture technique and make the next run closer to the changed surface. The configured
test-run cap closes one campaign: preserve prior passes, isolate the unmet
checks, and start a focused recovery campaign only when a fresh cap
assessment names a direct strategy not yet tried; each further campaign needs
another such assessment. When none remains, the assessment records recovery
exhaustion, and the task is split, moved to the improve lane for a threshold
the code cannot meet, or set aside with a `Block`. Nobody waits for the
owner. A cap and a `TEST_FLAW` can never by themselves publish `BLOCKED` or
approval.

A pre-Runner crash or DeadlockDetector event is not an evidence setup failure merely
because the scenario did not start. Apply the shared crash diagnostics and debugger
fallback before changing an account fixture. An empty or unusable dump requires live
debugging after at most one confirmation run; it never supports a fixture verdict.

A locked macOS session is not an environment stop or evidence blocker for a
selected Telegram runtime check. Skip interactive Computer Use and complete
the same coverage through the in-binary overlay: drive the flow, log/assert,
capture widgets or windows, quit, and assess the saved artifacts. Non-app
instruments are unaffected.

A Windows build-output lock is not an immediate environment stop. Follow the
shared bounded recovery contract, including exact-path cleanup before builds
and its delayed retries. Only its exhausted or unsafe outcome stops the run;
it never becomes a task `Block`.

The global stops that remain are a server scheme mismatch (the server rejects
the client's constructors, for example `INPUT_CONSTRUCTOR_INVALID`, or answers
with constructors the client cannot parse: the owner must update the scheme)
and a missing golden test account that cannot be recreated unattended. Dirty
source the run cannot account for, once the task has started, is never a
reason to stop: save it with
`workspace.py wip-save --reason "<what is unknown>"`, which commits it to a
new side branch `wip/<branch>/<checkout-tag>/<time>` (tdesktop on `private`,
submodules on their `origin`) and leaves the checkout clean, name that branch
in the result, and continue.
>>>>>>> v7.4.4

Do not report success from a source commit alone. The final AI commit must be
canonical. Retry ordinary concurrent-master publication races until success.
On a semantic conflict, unsafe checkout, or unreachable remote, preserve
resumable state and report a hard stop.

Return a compact result with the full task id, status or hard stop, attempts,
touched files, canonical final-publication confirmation, and exact evidence or
unverified behavior. Never persist or report commit hashes; the full task id is
the only cross-repository link.
