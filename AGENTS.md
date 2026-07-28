# Project agent instructions

## Repo docs

The living project guide is in `repo-docs/`. Start with
`repo-docs/README.md`; use `repo-docs/walkthroughs/guided-scan.md` as the guided
multi-screenshot trace and `repo-docs/walkthroughs/one-real-run.md` as the
standalone-capture trace.

Repo questions, architecture/onboarding answers, behavior-bearing code or test
edits, user corrections about stable behavior, and durable knowledge discovered
in conversation are repo-docs Sync triggers before the final response. Use the
`repo-docs` skill in Sync mode when available; otherwise read the relevant guide
and current source, then patch the smallest stale or missing page before answering
when the mismatch would mislead.

Broader non-answer-critical sync may be delegated to a tracked background agent
when available. Otherwise make a scoped foreground patch or report the pending
sync. Record meaningful guide changes and verification in
`repo-docs/change-log.md`, including `Synced through <sha>` when Git has a commit.

Device selection rules, exit codes, scan IDs, manifest fields/statuses, capture
layouts, and privacy boundaries are durable project knowledge and must remain synchronized.
