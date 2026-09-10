# Prediction-to-OpenRewrite generation

This directory contains the repository-independent prediction concretization
engine, candidate recipes, and validated recipes used by experiments.

## Why generation is necessary

The model CSV contains recommendation labels such as `Move Class`, but
OpenRewrite needs concrete parameters such as the old and new fully qualified
type names. `generate_recipes.py` inspects the supplied Java repository and
attempts to infer those parameters from semantic Java evidence. Existing direct
reciprocal import analysis remains the fallback for Move Class.

It ranks repository-backed candidates and records the evidence, model rank,
model score, structural score, and risk level for every emitted move.

## Generate recipes for any repository

```bash
scripts/generate_openrewrite_recipes.sh \
  --repository /absolute/path/to/java-repository \
  --predictions /absolute/path/to/predictions.csv \
  --output-dir /absolute/path/to/generated-openrewrite
```

The default CSV columns are:

```text
architecture_smell
affected_elements
suggestions
```

Affected elements are separated by `|`. Different schemas are supported:

```bash
scripts/generate_openrewrite_recipes.sh \
  --repository /path/to/repository \
  --predictions /path/to/predictions.csv \
  --output-dir /path/to/output \
  --smell-column smell_name \
  --elements-column packages \
  --suggestions-column model_output \
  --elements-separator ';'
```

## Generated output

```text
generated-openrewrite/
├── manifest.json
├── manifest.csv
├── all-candidates.yml
└── recipes/
    ├── prediction-0001-move-example.yml
    └── ...
```

The manifest contains every prediction, including predictions that cannot be
executed safely. Each record also contains `severity`, `severity_score`, and
`severity_reason`. `--severity-categories high,medium,low` controls which
categories are concretized; unselected records remain in the manifest as
`deferred_severity`.

Possible statuses are:

- `ready_for_dry_run`: one source class and destination package were inferred;
- `unsafe_destination`: a move crosses modules, targets tests, or conflicts with an existing type;
- `duplicate`: another prediction already selected the same source class;
- `unresolved`: repository evidence is insufficient for an executable move.
- `unresolved_method` / `unresolved_destination`: member or destination evidence is insufficient;
- `unsafe_source_state`: moving the member would preserve excessive source-state coupling;
- `cross_module`: source and target belong to different Maven modules;
- `destination_conflict`: the target already declares the method signature;
- `unsupported_refactoring`: no implemented resolver exists for the labels.

## Automated support and Move Method constraints

| Refactoring | Status |
|---|---|
| Move Class | Supported; semantic evidence where available and reciprocal-import fallback |
| Move Method | Conservative public-static subset supported |
| Extract Method | Unsupported |
| Move Attribute | Unsupported |
| Move+Rename | Unsupported |

Move Method requires an exact source class, resolved signature, and existing
destination class. The method must be public and static, non-constructor,
non-abstract, non-native, and non-synchronized. Source and destination must use
the same Maven module and source set; the destination must not contain the same
signature; foreign affinity must be at least 0.60; structural score at least
0.70; there must be no resolved source-state reference; and every non-JDK
dependency required by the method must belong to the destination package. Because the supported method is
public, normal validation sends it to manual review unless risky-candidate
execution is explicitly enabled. That option does not bypass build checks.

`scripts/run_semantic_analysis.sh` runs the non-mutating OpenRewrite
`DependencyAnalysisRecipe`, exports its DataTable, and writes
`results/semantic-analysis/method-dependencies.json`. If semantic analysis
fails, a warning is retained: Move Class may use its import fallback, while
Move Method remains unresolved.

## Selection algorithm

For each prediction, the generator:

1. resolves the affected packages at the checked-out revision;
2. builds class-level import dependencies between those packages;
3. finds direct reciprocal package pairs;
4. examines the imported classes responsible for each direction;
5. prefers the lower-weight dependency direction;
6. searches the complete ranked model output for a supported recommendation;
7. emits every distinct, ranked source/destination pair it can concretize;
8. prevents the same source class from being selected twice.

This is conservative by design. Compilation, tests, API compatibility, and smell
measurement are still required after every generated recipe.

## Model and automation limits

Model training is separate from concretization and is unchanged by this work.
All input rows receive five ranked suggestions. The generator executes
Move Class and only the explicitly constrained Move Method subset. Extract
Method remains unsupported because it requires statement-range selection,
control-flow analysis, parameter and return extraction, and exception-flow
analysis. Unsupported predictions remain visible instead of becoming recipes.

Run Java recipe tests with Maven 3.9+ and JDK 17:

```bash
mvn -f openrewrite-java/pom.xml test
```

More candidates do not imply more correct refactorings. Each candidate is tested
in isolation and classified as validated, not applicable, compilation failure,
API-compatibility failure, module-metadata failure, dependency-resolution
failure, test failure, or tooling/build failure.

## Aggregate execution recipe

`all-candidates.yml` combines every `ready_for_dry_run` record for review. The
pipeline validates each member in an isolated Git worktree and builds
`openrewrite-validation/validated-candidates.yml` from candidates whose
post-rewrite reactor compilation passes. That validated aggregate—not the raw
candidate aggregate—is applied to the experiment tree.

For the assigned Log4j2 revision, the production-to-test safety rule excludes
the candidates known to break package-local access. The remaining aggregate was
validated by applying it to a disposable clean worktree and running the full
reactor compile with tests skipped; final pipeline verification still runs after
application.
