# Domain docs

## Layout and reading rules

This repo uses a single domain context:

- `GLOSSARY.md` at the repo root defines domain terms. Read it before exploring the codebase.
- `docs/adr/` holds architecture decision records. Read the records relevant to the area being explored or changed.

If either is absent, proceed silently. The `domain-modeling` skill creates these documents when terms or decisions are resolved.

## Vocabulary

Use glossary terms in issue titles, proposals, hypotheses, and test names. If a needed term is missing, check existing project usage and note a real gap for `domain-modeling`.

## Decision conflicts

If a proposal contradicts an existing ADR, name the record and explain why the decision should be revisited.
