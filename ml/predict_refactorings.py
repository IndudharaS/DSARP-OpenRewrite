#!/usr/bin/env python3
"""Export the model's unconditional top-k ranked refactoring suggestions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-inputs", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    frame = pd.read_csv(args.model_inputs)
    if "input_text" not in frame:
        raise SystemExit("Model-input CSV is missing input_text")
    metadata = json.loads((args.model_dir / "labels.json").read_text(encoding="utf-8"))
    id2label = {int(k): str(v) for k, v in metadata["id2label"].items()}
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(args.model_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()

    output = []
    with torch.no_grad():
        for _, row in frame.iterrows():
            encoded = tokenizer(str(row["input_text"]), return_tensors="pt", truncation=True,
                                padding=True, max_length=int(metadata.get("max_length", 256)))
            encoded = {key: value.to(device) for key, value in encoded.items()}
            scores = torch.sigmoid(model(**encoded).logits[0]).cpu().numpy()
            ranked_indices = scores.argsort()[::-1][:args.top_k]
            ranked = [(id2label[int(i)], float(scores[i])) for i in ranked_indices]
            result = {key: row.get(key, "") for key in
                      ("project", "versionId", "architecture_smell", "affected_elements", "input_text")}
            result.update({
                "suggestions": " | ".join(f"{label} ({score:.3f})" for label, score in ranked),
            })
            output.append(result)

    result_frame = pd.DataFrame(output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result_frame.to_csv(args.output, index=False)
    print(f"Saved top-{args.top_k} ranked suggestions for {len(result_frame)} records: {args.output}")


if __name__ == "__main__":
    main()
