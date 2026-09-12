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

Semantic analysis prepares the unchanged target Maven reactor before the
OpenRewrite dry run. This makes project-local SNAPSHOT dependencies available
to aggregator modules. Move Class candidates that still depend on a type in
their original package are rejected before execution; this prevents the
unresolved-symbol failure previously observed after moving `AppenderWrapper`.
Move Class is also rejected when its fully qualified type name appears in
non-Java metadata or a resource path such as `META-INF/services`. `ChangeType`
updates Java references but cannot safely migrate all service registrations,
reflection configuration, or resource contracts. Isolated validation now runs
directly affected Java tests after compilation, catching behavioural failures
such as a ServiceLoader returning no providers before aggregation.
The non-Java metadata corpus is read once per generation run and reused for all
candidates; repository files are not rescanned for every prediction.
Public production classes are not emitted for expensive validation unless the
move has an implemented compatibility strategy. Public types under a test
source set are recorded as `test_only`, while package-private types are
`internal_only`; the latter are preferred across the complete ranked candidate
list. If only unsupported public production types remain, the manifest records
`unsafe_public_api` and `no safe internal candidate` rather than creating a
recipe that is expected to fail API verification.

When isolated validation accepts zero candidates, the aggregate worktree is
unchanged. The pipeline records `results/post-validation-skip.json` and skips
focused tests, formatting, final full-reactor verification, and the second
Arcan scan. Those stages cannot add evidence for an unchanged repository and
previously accounted for hours of unnecessary runtime.

Log4j2's `RollingAppenderDirectCronTest` requires a newly rolled file to become
non-empty within two seconds. The HPC parallel filesystem can miss that
visibility deadline although the rollover completes. A baseline/final reactor
is accepted only when this is the sole failed test, its isolated retry produces
the same rollover/timeout signature, and no compilation or unrelated test
failure is present. The exception is intentionally narrower than accepting a
generic Maven test failure.

Move Method adds semantic gates before this protocol: the resolver must identify
an exact attributed method and existing destination class, retain affinity and
source-state components in the manifest, and reject module/source-set and
signature conflicts. The first executable subset is public static methods.
They still enter `manual_review` unless risky-candidate execution is explicitly
enabled; enabling it does not bypass meaningful-diff, formatting, Maven,
aggregate, or Arcan gates. A vague `Move Method` label never reaches execution.

When multiple methods are available, executable candidates are ranked before
higher-scoring candidates that fail source-state, destination, visibility, or
dependency-context gates. Public test-source methods are recorded as
`test_only`; production public methods still require explicit risk approval.

Move Class resolution considers reciprocal dependencies first and then one-way
semantic/import dependencies. The latter broadens the search for safe internal
types in hub-like and unstable-dependency smells without weakening module,
source-set, metadata, destination-conflict, or original-package dependency gates.

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

The dashboard option **Include evidence-backed Log4j2 FileSize refactoring**
adds this as `candidate_origin=curated_evidence`. It is intentionally separate
from model predictions, runs first in isolated validation, installs the known
compatibility facade, and must still pass Maven, focused-test, diff, API, and
final verification gates. It is rejected for non-Log4j2 systems.

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
