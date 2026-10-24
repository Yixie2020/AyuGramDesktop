---
description: Continue work from the shared ai-tdesktop queue
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, AskUserQuestion, TodoWrite
---

# Continue AI Work

Read `.claude/ai-workflow-adapter.md` and
`.agents/skills/continue/SKILL.md` completely. Follow the shared skill with the
Claude adapter's delegation and text-handling substitutions. The arguments
below are the required batch specification. Without one, stop after the
read-only `queue` call, report what the shared skill lists, and ask for a
specification through `AskUserQuestion`. Otherwise choose exactly one
shared-skill mode and record its frozen task ids: any active task first, then
exactly the tasks the specification selects, or the tasks one inbox
transaction routes when it asks for the inbox. "All pending tasks" freezes the
pending tasks on canonical ai-tdesktop master at startup. Never add unrelated
tasks observed later in the run. Append and implement only new follow-up
tasks routed from results produced by this invocation, including their
transitive discovered follow-ups.
Preserve the shared skill's source-project inheritance rule; detaching a
follow-up requires its affirmative independence test. Continue until that
frozen-and-derived batch reaches the scheduler's normal stop condition or a
global hard stop.

Every inbox worker, performer, and discovered-task routing Agent must be told
in its initial prompt to read `.claude/ai-workflow-adapter.md` completely before
the applicable shared skill or reference. Use the Agent tool for those workers;
do not start Claude subprocesses through Bash.

Decide the review provider once at startup, per the adapter's review and
assessment provider: `native` when the arguments ask not to use Codex,
otherwise `codex`. Put `Review provider: <codex|native>` in every performer
prompt. A Codex opt-out is not a batch specification.

The arguments are the batch specification. They always restrict the batch to
the tasks they select; ordering words only order those tasks:

```text
$ARGUMENTS
```
