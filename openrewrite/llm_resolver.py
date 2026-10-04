"""Bounded OpenAI-compatible LLM client for repository candidate selection.

The model never supplies arbitrary source symbols.  It selects identifiers from
an evidence packet produced by the deterministic repository analysis; callers
must still enforce structural and build-validation gates.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = """You resolve architecture-refactoring predictions into concrete Java candidates.
Use only identifiers present in the supplied evidence. Never invent a class, package, method, or
signature. Prefer reject when evidence is insufficient. Return one JSON object only, without
Markdown, with keys operation, source_type, destination_type, source_signature, confidence,
reason. operation must be move_class, move_method, or reject. For move_class destination_type
is the proposed fully-qualified new type name. For move_method it is an existing target class."""


@dataclass(frozen=True)
class LlmProposal:
    operation: str
    source_type: str | None
    destination_type: str | None
    source_signature: str | None
    confidence: float
    reason: str


class LlmResolver:
    def __init__(self, endpoint: str, model: str, output_dir: Path, *, timeout: int = 120,
                 max_calls: int = 20, min_confidence: float = 0.65) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.output_dir = output_dir
        self.timeout = timeout
        self.max_calls = max_calls
        self.min_confidence = min_confidence
        self.calls = 0
        output_dir.mkdir(parents=True, exist_ok=True)

    def resolve(self, prediction_id: int, evidence: dict[str, Any]) -> LlmProposal | None:
        if self.calls >= self.max_calls:
            return None
        request_body = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(evidence, sort_keys=True)},
            ],
        }
        encoded = json.dumps(request_body, sort_keys=True).encode("utf-8")
        request_hash = hashlib.sha256(encoded).hexdigest()
        artifact = self.output_dir / f"prediction-{prediction_id:04d}.json"
        if artifact.is_file():
            saved = json.loads(artifact.read_text(encoding="utf-8"))
            if saved.get("request_sha256") == request_hash and saved.get("status") == "complete":
                return self._proposal(saved.get("response"))

        self.calls += 1
        started = time.monotonic()
        request = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions", data=encoded,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        saved: dict[str, Any] = {
            "prediction_id": prediction_id, "endpoint": self.endpoint,
            "model": self.model, "request_sha256": request_hash,
        }
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            parsed = self._extract_object(content)
            saved.update({"status": "complete", "response": parsed})
        except (OSError, KeyError, IndexError, TypeError, ValueError,
                urllib.error.URLError) as error:
            saved.update({"status": "error", "error": f"{type(error).__name__}: {error}"})
            parsed = None
        saved["elapsed_seconds"] = round(time.monotonic() - started, 3)
        artifact.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
        return self._proposal(parsed)

    def _proposal(self, value: Any) -> LlmProposal | None:
        if not isinstance(value, dict):
            return None
        operation = str(value.get("operation", "reject")).lower()
        if operation not in {"move_class", "move_method", "reject"}:
            return None
        try:
            confidence = float(value.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if not 0.0 <= confidence <= 1.0:
            return None
        proposal = LlmProposal(
            operation=operation,
            source_type=self._optional_string(value.get("source_type")),
            destination_type=self._optional_string(value.get("destination_type")),
            source_signature=self._optional_string(value.get("source_signature")),
            confidence=confidence, reason=str(value.get("reason", ""))[:1000],
        )
        return proposal if operation == "reject" or confidence >= self.min_confidence else None

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _extract_object(content: Any) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("LLM response content is not text")
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("LLM response must be a JSON object")
        return value
