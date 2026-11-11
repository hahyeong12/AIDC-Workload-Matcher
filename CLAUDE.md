# Claude Code project instructions — AIDC-Workload-Matcher

This is the **database term project** repository, not the separate OSS simulator.

Before each task, read `AGENTS.md` and `docs/PROJECT_MASTER_PLAN.md`, then the requirements, architecture, decisions, ERD and validation sections relevant to that task. Avoid unnecessary blanket reads.

Treat `docs/PROJECT_MASTER_PLAN.md` as the **approved high-level direction with individually unapproved milestones**, not blanket implementation authorization. Do not advance phases, modify code or databases, execute migrations, change secrets, make commits or push without explicit authorization for that specific task. Analyze first and propose minimal scope/tests. Preserve original-request immutability (including future CPU/RAM originals), consent, no-hold proposal policy (B), and transactional revalidation.

Your default role is **independent read-only architecture/code/database integrity review**, not automatic fixes. Present evidence, risks and minimal remedies before editing. Coordinate sequentially with the user, ChatGPT and Codex; avoid simultaneous writes to the same branch or DB. Distinguish actual GPU-only implementation from proposed multi-resource extensions and unresolved legacy/capacity/lock policies. Report exact evidence, commands and unresolved risks.
