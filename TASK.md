# Implement F2 — one spend gate inside SocialDataClient._request in SablePlatform

## Where you are

A dedicated git worktree on its own branch, cut from the audited base ref. It is a
complete standalone checkout. Work only here.

**You cannot commit.** A linked worktree's real `.git` lives in the parent repo, so
staging is denied by the sandbox. That is expected. **Leave your changes uncommitted.**
The orchestrator reads `git diff` and commits. Do not fight this and do not try to
work around it.

## What to build

F2 — one spend gate inside SocialDataClient._request, tasks the F2 tasks in the plan.

The plan is at `/Users/sieggy/sable-workspace/features/megaloop_features_2026-08-26/codex/OUT_PLAN_B_r3.md`. It carries, per acceptance test: the test body, why it fails
today, and what wrong implementation would still pass it. **Read the whole feature section
before you touch anything.**

Tasks run IN ORDER. Each task's job is to make exactly one acceptance test pass. A later
task must see the earlier task's work, so do not reorder them.

## The limits file is not optional reading

`/Users/sieggy/sable-workspace/features/megaloop_features_2026-08-26/KNOWN_TEST_LIMITS_SablePlatform.md`

Some of these tests GO GREEN ON A WRONG IMPLEMENTATION. The file names which ones and what
the wrong implementation looks like. **For every limit that touches a test you turn green,
check the named wrong implementation BY HAND and say in your report that you did.**

A green suite on those tests is necessary and not sufficient. If your report implies
otherwise it is wrong.

## The baseline

SablePlatform: 0 failing, 3208 passing on the F1 branch. Note F1 (item metering) is ALREADY MERGED into your base, so build on top of it.

A test failing there is INHERITED, not yours. Do not fix it, do not depend on it, and do
not count it against yourself. A test that fails ONLY after your change is a regression and
blocks the work.

## Rules

- Build what the plan says. Do not improve on it, widen it, or fix things it does not name.
  The plan's scope was audited; your additions were not.
- The plan states `MUST_NOT_CHANGE` per test. Check each before you finish.
- Any `## Open decisions` in the plan stay OPEN. Do not invent an answer to one.
- If the plan does not fit the code, STOP that task and report the mismatch with a
  `file:line`. Do not improvise a different design. A plan that no longer matches the code
  is a real finding.

## Report

Write `IMPL_REPORT.md` in this directory:

```
## <task id> — <test it greens>
STATUS: DONE | BLOCKED
CHANGED: <files>
TEST_RESULT: <exact command, exact counts, red-before and green-after>
MUST_NOT_CHANGE_VERIFIED: <how you checked>
LIMIT_CHECKED_BY_HAND: <which limit, what you checked, what you found — or N/A>
DEVIATION: <anything you did differently and why, or NONE>
```

End with `## Summary`: tasks done, tasks blocked, the suite result, and one line naming
anything a reviewer should look at closely.

**Never report a test result you did not obtain.** If a suite cannot run, say so and say
why. That is a correct and useful answer. Inventing one is the worst outcome available to
you, and it has happened on this estate before.
