#!/usr/bin/env python3
"""Export calibrated, thresholded multilabel refactoring suggestions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from model_quality import select_predictions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-inputs", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    frame = pd.read_csv(args.model_inputs)
    if "input_text" not in frame:
        raise SystemExit("Model-input CSV is missing input_text")
    metadata = json.loads((args.model_dir / "labels.json").read_text(encoding="utf-8"))
    id2label = {int(k): str(v) for k, v in metadata["id2label"].items()}
    ordered_labels = [id2label[index] for index in range(len(id2label))]
    thresholds = metadata.get("decision_thresholds", {})
    calibrations = metadata.get("calibration", {})
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(args.model_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()

    output = []
    with torch.no_grad():
        for start in range(0, len(frame), args.batch_size):
            batch = frame.iloc[start:start + args.batch_size]
            encoded = tokenizer(batch["input_text"].astype(str).tolist(), return_tensors="pt", truncation=True,
                                padding=True, max_length=int(metadata.get("max_length", 256)))
            encoded = {key: value.to(device) for key, value in encoded.items()}
            score_rows = torch.sigmoid(model(**encoded).logits).cpu().numpy()
            for (_, row), scores in zip(batch.iterrows(), score_rows):
                selected = select_predictions(scores, ordered_labels, thresholds, calibrations, args.top_k)
                result = {key: row.get(key, "") for key in
                          ("project", "versionId", "architecture_smell", "affected_elements", "input_text")}
                result.update({
                    "prediction_status": "recommended" if selected else "abstained_low_confidence",
                    "selected_count": len(selected),
                    "top_calibrated_score": f"{selected[0][1]:.6f}" if selected else "",
                    "suggestions": " | ".join(f"{label} ({score:.3f})" for label, score, _ in selected),
                    "suggestion_details_json": json.dumps([
                        {"label": label, "calibrated_score": calibrated, "raw_score": raw,
                         "threshold": float(thresholds.get(label, 0.5))}
                        for label, calibrated, raw in selected
                    ]),
                })
                output.append(result)

    result_frame = pd.DataFrame(output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result_frame.to_csv(args.output, index=False)
    abstained = int((result_frame["prediction_status"] == "abstained_low_confidence").sum())
    print(f"Saved thresholded suggestions for {len(result_frame)} records ({abstained} abstained): {args.output}")


if __name__ == "__main__":
    main()
