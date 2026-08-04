# Industry readiness checklist

The platform currently provides guarded experimental automation, not an
autonomous refactoring service.

## Implemented controls

- exact repository revision verification;
- validated input schemas and project/version identity;
- shared mining-cache locking and explicit remine behavior;
- evidence-backed training-row filtering and rare-label filtering;
- commit-grouped train/validation/test splitting;
- held-out ranking metrics with an explicit qualification verdict;
- full ranked-model output retained for audit;
- concrete recipe parameter and risk manifests;
- public-API moves blocked without a registered compatibility strategy;
- isolated Git worktree validation;
- relevant-source-change/no-op detection;
- compilation, API, dependency, module, test, and tool failure categories;
- final project verification;
- matched Arcan before/after execution;
- changed-file and artifact-hash evidence report;
- CLI and localhost web execution using the same pipeline.

## Required before production deployment

- expand and independently label the training corpus;
- evaluate on repositories excluded from mining and model development;
- report confidence intervals and per-project/per-smell metrics;
- register project-specific build, test, and compatibility policies;
- support non-Maven builds if required by the target organization;
- add authentication, authorization, job isolation, quotas, and persistent job
  storage before exposing the dashboard beyond localhost;
- run in disposable containers with controlled network and dependency caches;
- add software-composition, license, and security scanning;
- obtain human approval before creating a branch, commit, or pull request;
- perform a study comparing accepted recommendations against expert judgment.

The software should be presented to industry as a traceable decision-support
and validation platform. Its current model must not be marketed as generally
accurate or safe for unattended source modification.
