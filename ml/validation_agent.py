#!/usr/bin/env python3
"""Rule-based validation agent for ranked refactoring-type suggestions.

``ml/predict_refactorings.py`` writes a ranked ``suggestions`` column such as
``Move Class (0.870) | Extract Interface (0.650) | ...``. Historically only
the first ``Move Class`` entry was ever used (see
``openrewrite/generate_recipes.py``), and the model's held-out evaluation
shows majority-label collapse toward ``Move Class`` (see
``docs/VALIDATION.md``) — so trusting rank/confidence alone is not reliable
evidence.

This agent instead looks at every ranked candidate label for a row, discards
the confidence score entirely, and applies a documented, deterministic
smell-to-refactoring compatibility matrix to decide which candidate is
structurally the better fit for the specific architecture smell reported in
that row, and whether any candidate is even capable of resolving it.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


RANKED_SUGGESTION_RE = re.compile(r"^\s*(.*?)\s*(?:\([0-9]*\.?[0-9]+\))?\s*$")

# Weight 2: the refactoring directly addresses the smell's structural cause.
# Weight 1: the refactoring can make a plausible, secondary contribution.
# Absent (0): not considered applicable to this smell.
SMELL_REFACTORING_COMPATIBILITY: dict[str, dict[str, int]] = {
    # Breaking a cycle requires relocating or abstracting one edge of it so
    # both sides no longer depend directly on each other.
    "cyclic_dependency": {
        "Move Class": 2,
        "Move And Rename Class": 2,
        "Move Source Folder": 2,
        "Split Package": 2,
        "Extract Interface": 2,
        "Move Method": 1,
        "Move Attribute": 1,
        "Move And Rename Method": 1,
        "Extract And Move Method": 1,
    },
    # Fixing an over-connected hub requires decomposing its responsibilities
    # so dependents can attach to smaller, more focused pieces.
    "hub_like_dependency": {
        "Extract Class": 2,
        "Extract Interface": 2,
        "Split Package": 2,
        "Move Method": 1,
        "Move Attribute": 1,
        "Extract And Move Method": 1,
        "Pull Up Method": 1,
        "Pull Up Attribute": 1,
        "Push Down Method": 1,
        "Push Down Attribute": 1,
        "Extract Superclass": 1,
        "Extract Subclass": 1,
    },
    # Stabilizing a dependency requires depending on an abstraction instead
    # of the volatile concrete type (dependency inversion).
    "unstable_dependency": {
        "Extract Interface": 2,
        "Extract Superclass": 2,
        "Extract Subclass": 1,
        "Move Class": 1,
        "Move And Rename Class": 1,
        "Move Source Folder": 1,
        "Extract Class": 1,
        "Pull Up Method": 1,
        "Pull Up Attribute": 1,
    },
}

SmellSolvable = Literal["yes", "no", "unsupported_smell_type"]


@dataclass
class AgentDecision:
    prediction_id: int
    architecture_smell: str
    normalized_smell: str | None
    candidates_considered: list[str]
    chosen: str | None
    smell_solvable: SmellSolvable
    reasoning: str
    compatible_candidates: list[str]
    rejected_candidates: list[tuple[str, str]]


def normalize_smell(smell_text: str) -> str | None:
    """Match free-text smell labels to a known compatibility-matrix key.

    Uses the same keyword approach as
    ``openrewrite.generate_recipes.classify_severity`` so smell
    classification stays consistent across the pipeline.
    """
    normalized = smell_text.lower()
    if "cyclic" in normalized or "cycle" in normalized:
        return "cyclic_dependency"
    if "hub" in normalized:
        return "hub_like_dependency"
    if "unstable" in normalized:
        return "unstable_dependency"
    return None


def parse_candidates(raw: str) -> list[str]:
    """Parse ranked ``Label (score) | Label (score)`` suggestions.

    The confidence score is parsed only so it can be discarded — it is never
    inspected or compared. Labels are deduplicated, preserving the order
    they first appear in.
    """
    if not raw or str(raw).lower() == "nan":
        return []
    labels: list[str] = []
    for value in str(raw).split("|"):
        match = RANKED_SUGGESTION_RE.match(value)
        if not match:
            continue
        label = match.group(1).strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def evaluate_row(prediction_id: int, smell_text: str, raw_suggestions: str) -> AgentDecision:
    candidates = parse_candidates(raw_suggestions)
    normalized = normalize_smell(smell_text)

    if normalized is None:
        return AgentDecision(
            prediction_id=prediction_id,
            architecture_smell=smell_text,
            normalized_smell=None,
            candidates_considered=candidates,
            chosen=None,
            smell_solvable="unsupported_smell_type",
            reasoning=f"'{smell_text}' is not a recognized smell type; no compatibility rule exists for it.",
            compatible_candidates=[],
            rejected_candidates=[(label, "unsupported smell type") for label in candidates],
        )

    weights = SMELL_REFACTORING_COMPATIBILITY[normalized]
    scored = [(label, weights.get(label, 0)) for label in candidates]
    compatible = sorted(
        (item for item in scored if item[1] > 0),
        key=lambda item: (-item[1], candidates.index(item[0])),
    )
    rejected = [
        (label, "not associated with resolving this smell type" if weight == 0
         else "lower-priority candidate than the chosen refactoring")
        for label, weight in scored
        if not compatible or label != compatible[0][0]
    ]

    if compatible:
        chosen, weight = compatible[0]
        reasoning = (
            f"'{chosen}' has the highest compatibility weight ({weight}) for "
            f"'{smell_text}' among the ranked candidates {candidates}; "
            "the model's confidence score was not used in this decision."
        )
        return AgentDecision(
            prediction_id=prediction_id,
            architecture_smell=smell_text,
            normalized_smell=normalized,
            candidates_considered=candidates,
            chosen=chosen,
            smell_solvable="yes",
            reasoning=reasoning,
            compatible_candidates=[label for label, _ in compatible],
            rejected_candidates=rejected,
        )

    reasoning = (
        f"None of the ranked candidates {candidates} are known to resolve "
        f"'{smell_text}'; the smell is not considered solvable by this prediction."
    )
    return AgentDecision(
        prediction_id=prediction_id,
        architecture_smell=smell_text,
        normalized_smell=normalized,
        candidates_considered=candidates,
        chosen=None,
        smell_solvable="no",
        reasoning=reasoning,
        compatible_candidates=[],
        rejected_candidates=rejected,
    )


def evaluate(args: argparse.Namespace) -> dict:
    with args.predictions.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    required = {args.smell_column, args.suggestions_column}
    missing = required.difference(rows[0].keys() if rows else set())
    if missing:
        raise SystemExit(f"Prediction CSV is missing columns: {sorted(missing)}")

    output_fields = list(rows[0].keys()) if rows else []
    for extra in ("agent_selected_refactoring", "agent_smell_solvable", "agent_reasoning",
                  "agent_candidates_considered", "agent_rejected_candidates"):
        if extra not in output_fields:
            output_fields.append(extra)

    decisions: list[AgentDecision] = []
    output_rows: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        decision = evaluate_row(index, row[args.smell_column], row[args.suggestions_column])
        decisions.append(decision)
        output_row = dict(row)
        output_row["agent_selected_refactoring"] = decision.chosen or ""
        output_row["agent_smell_solvable"] = decision.smell_solvable
        output_row["agent_reasoning"] = decision.reasoning
        output_row["agent_candidates_considered"] = " | ".join(decision.candidates_considered)
        output_row["agent_rejected_candidates"] = " | ".join(
            f"{label} ({reason})" for label, reason in decision.rejected_candidates
        )
        output_rows.append(output_row)

    smell_counts: dict[str, int] = {}
    solvable_counts: dict[str, int] = {}
    for decision in decisions:
        key = decision.normalized_smell or "unrecognized"
        smell_counts[key] = smell_counts.get(key, 0) + 1
        solvable_counts[decision.smell_solvable] = solvable_counts.get(decision.smell_solvable, 0) + 1

    report = {
        "predictions": str(args.predictions.resolve()),
        "record_count": len(decisions),
        "smell_type_counts": smell_counts,
        "smell_solvable_counts": solvable_counts,
        "decisions": [asdict(decision) for decision in decisions],
    }

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(output_rows)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--smell-column", default="architecture_smell")
    parser.add_argument("--elements-column", default="affected_elements")
    parser.add_argument("--suggestions-column", default="suggestions")
    args = parser.parse_args()

    report = evaluate(args)
    print(json.dumps({key: value for key, value in report.items() if key != "decisions"}, indent=2))
    print(f"Validated predictions CSV: {args.output_csv}")
    print(f"Validation agent report:   {args.output_json}")


if __name__ == "__main__":
    main()
