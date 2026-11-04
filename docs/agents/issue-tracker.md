# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `AIDroneMap/aidm-windows-deps`. Use the `gh` CLI from this repo's checkout, or pass `--repo AIDroneMap/aidm-windows-deps` from elsewhere.

## Issue operations

- Create: `gh issue create --title "..." --body-file <file>`.
- Read: `gh issue view <number> --json number,title,body,labels,comments`.
- List: `gh issue list --state open --json number,title,body,labels,comments`. Add label filters as needed.
- Comment: `gh issue comment <number> --body-file <file>`.
- Apply or remove labels: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`. Use the mapping in `triage-labels.md`.
- Close: `gh issue close <number> --comment "..."`.

When a skill says "publish to the issue tracker", create a GitHub issue. When it says "fetch the relevant ticket", read that issue and its comments.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares issue and PR numbers. If a referenced number is a PR, read its description, comments, and diff with `gh pr view <number> --comments` and `gh pr diff <number>`.

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`, containing Notes, Decisions-so-far, and Fog.
- Child ticket: link an issue with `gh issue create --parent <parent> ...` or `gh issue edit <parent> --add-sub-issue <child>`. If sub-issues are unavailable, add the child to the map's task list and put `Part of #<parent>` at the top of the child body.
- Ticket type: use `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`. Create a label if needed before using it.
- Blocking: use native dependencies with `gh api --method POST repos/AIDroneMap/aidm-windows-deps/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`. Get the database ID with `gh api repos/AIDroneMap/aidm-windows-deps/issues/<blocker> --jq .id`. If dependencies are unavailable, put `Blocked by: #<number>` at the top of the child body.
- Frontier: choose the first open child in map order with no assignee and no open blockers. Native `issue_dependencies_summary.blocked_by` counts open blockers. With text dependencies, check whether each referenced blocker is closed.
- Claim: `gh issue edit <number> --add-assignee @me` before starting work.
- Resolve: comment with the findings, close the child, and add a short finding with its link to the map's Decisions-so-far.
