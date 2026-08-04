# Validation protocol and current Log4j2 evidence

## Protocol

A candidate is automatically applicable only when all gates pass:

1. the affected packages and source type exist at the exact requested commit;
2. the recipe has concrete, repository-derived parameters;
3. test/production and module boundaries are safe;
4. a public API move has an explicitly registered compatibility strategy;
5. OpenRewrite produces relevant source/build-file changes;
6. formatting and `git diff --check` pass;
7. isolated Maven verification and configured API checks pass;
8. the validated aggregate passes project-level verification;
9. baseline and refactored bytecode are measured by the same pinned Arcan build
   with the same options;
10. the evidence report confirms the revision, source diff, artifact hashes,
    and whether a causal result may be stated.

## Assigned Logging-Log4j2 revision

Revision: `4f474b32751f4ccad67424ca585612584440cd63`.

The corrected focused validation generated 40 concrete candidates. Thirty-nine
public-API moves were routed to manual review because no compatibility strategy
was registered. The `FileSize` migration had a compatibility facade and passed
the complete isolated Maven reactor verification:

```text
BUILD SUCCESS
Total time: 02:24 min
validated: 1
manual review: 39
failed: 0
```

The validated change moves
`org.apache.logging.log4j.core.appender.rolling.FileSize` to
`org.apache.logging.log4j.core.appender.rolling.action.FileSize`, updates its
usages/tests, and retains the old public API through a compatibility facade.

Matched Arcan 1.2.1 measurement produced:

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| Package cycles | 223 | 217 | -6 |
| Class cycles | 414 | 414 | 0 |
| Hub-like dependencies | 3 | 3 | 0 |
| Unstable dependencies | 130 | 129 | -1 |
| UD30 | 59 | 59 | 0 |

Cycle identities show seven resolved package-cycle sets, one introduced set,
and 216 unchanged sets. This is a mixed architectural result, not an assertion
that every smell improved.

## Model limitation

After evidence filtering, the model’s held-out set contained only 17 records. Its observed
ranking metrics were:

| Metric | Value |
|---|---:|
| Top-1 hit rate | 0.647 |
| Micro precision@5 | 0.212 |
| Micro recall@5 | 0.545 |
| Macro label recall@5 | 0.367 |

All 716 target records still ranked `Move Class` first, demonstrating majority-
label collapse despite weighting. This fails the configured autonomous-use
qualification threshold and is marked `research_only`. Build/API validation—not the model score—is what made the one
applied Log4j2 result trustworthy. A larger independently labelled corpus and
external validation are required before industrial recommendation-quality
claims are justified.
