#!/usr/bin/env python3
"""Zero-dependency local and Slurm dashboard for the refactoring pipeline."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import mimetypes
import os
import re
import signal
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "webui" / "state"
RUNS = STATE / "runs"
ASSETS = ROOT / "webui" / "static"
PIPELINE = ROOT / "scripts" / "run_generic_pipeline.sh"
PIPELINE_CACHE = ROOT / "shared" / "pipeline-cache"
HPC_SCRIPT = ROOT / "hpc" / "noctua_pipeline.sbatch"
EXECUTION_MODE = "local"
HPC_PROJECT_SPACE = Path(os.environ.get("DSARP_HPC_PROJECT_SPACE", f"/scratch/hpc-prf-dssecs/{os.environ.get('USER', '')}"))
HPC_RUNS_ROOT = HPC_PROJECT_SPACE / "runs"
STAGES = ["preflight", "inputs", "mining", "training", "prediction", "clone", "baseline",
          "rewrite", "focused_test", "format", "final_verify", "smells", "summary"]
PROCESSES: dict[str, subprocess.Popen[str]] = {}
LOCK = threading.Lock()
SUBMIT_LOCK = threading.Lock()
HPC_REFRESHING: set[str] = set()
NAME = re.compile(r"^[A-Za-z0-9._-]+$")
COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")
SLURM_ACTIVE = {"PENDING": "queued", "CONFIGURING": "queued", "RUNNING": "running",
                "COMPLETING": "running", "REQUEUED": "queued", "RESIZING": "running"}
SLURM_FAILED = {"FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL",
                "DEADLINE", "PREEMPTED", "REVOKED"}
RESUME_STAGES = ["baseline", "rewrite", "focused_test", "format", "final_verify", "smells", "summary"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        if error.msg != "Extra data":
            raise
        decoder = json.JSONDecoder()
        values: list[dict] = []
        position = 0
        while position < len(text):
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text):
                break
            item, position = decoder.raw_decode(text, position)
            if isinstance(item, dict):
                values.append(item)
        if not values:
            raise
        value = values[-1]
        atomic_json(path, value)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def metadata_path(run_id: str) -> Path:
    return RUNS / run_id / "metadata.json"


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def hpc_available() -> bool:
    return HPC_SCRIPT.is_file() and all(command_available(name) for name in ("sbatch", "squeue", "sacct", "scancel"))


def normalize_slurm_state(state: str) -> str:
    value = state.strip().upper().split("+")[0]
    if value in SLURM_ACTIVE:
        return SLURM_ACTIVE[value]
    if value == "COMPLETED":
        return "completed"
    if value.startswith("CANCELLED"):
        return "stopped"
    if value in SLURM_FAILED:
        return "failed"
    return "queued" if not value else "failed"


def slurm_status(job_id: str) -> dict:
    queued = subprocess.run(
        ["squeue", "-h", "-j", job_id, "-o", "%T|%M|%R"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    line = queued.stdout.strip().splitlines()
    if line:
        state, elapsed, reason = (line[0].split("|", 2) + ["", ""])[:3]
        return {"slurmState": state, "status": normalize_slurm_state(state),
                "slurmElapsed": elapsed, "slurmReason": reason, "exitCode": None}
    accounting = subprocess.run(
        ["sacct", "-n", "-X", "-P", "-j", job_id,
         "--format=State,Elapsed,ExitCode"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    records = [value for value in accounting.stdout.strip().splitlines() if value.strip()]
    if not records:
        return {"slurmState": "ACCOUNTING_PENDING", "status": None, "exitCode": None}
    state, elapsed, exit_code = (records[0].split("|", 2) + ["", ""])[:3]
    numeric_exit = None
    try:
        numeric_exit = int(exit_code.split(":", 1)[0])
    except ValueError:
        pass
    return {"slurmState": state, "status": normalize_slurm_state(state),
            "slurmElapsed": elapsed, "slurmReason": "", "exitCode": numeric_exit}


def refresh_hpc_run(data: dict) -> dict:
    if data.get("status") not in {"queued", "running"}:
        return data
    update = slurm_status(str(data["slurmJobId"]))
    if update.get("status") is None:
        data["slurmState"] = update["slurmState"]
        return data
    old_status = data.get("status")
    data.update(update)
    if old_status != data["status"] and data["status"] in {"completed", "failed", "stopped", "interrupted"}:
        data["finishedAt"] = now()
        if data["status"] == "completed":
            cache_completed_artifacts(data)
    return data


def refresh_hpc_run_in_background(run_id: str) -> None:
    try:
        path = metadata_path(run_id)
        original = read_json_file(path)
        if original.get("status") not in {"queued", "running"}:
            return
        update = slurm_status(str(original["slurmJobId"]))
        if update.get("status") is None:
            update = {"slurmState": update["slurmState"]}
        with LOCK:
            current = read_json_file(path)
            old_status = current.get("status")
            current.update(update)
            if (old_status != current.get("status")
                    and current.get("status") in {"completed", "failed", "stopped", "interrupted"}):
                current["finishedAt"] = now()
                if current["status"] == "completed":
                    cache_completed_artifacts(current)
            atomic_json(path, current)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        # A slow/unavailable Slurm controller must not make the HTTP dashboard
        # unavailable. The next browser poll schedules another status refresh.
        pass
    finally:
        with LOCK:
            HPC_REFRESHING.discard(run_id)


def schedule_hpc_refresh(run_id: str) -> None:
    with LOCK:
        if run_id in HPC_REFRESHING:
            return
        HPC_REFRESHING.add(run_id)
    threading.Thread(target=refresh_hpc_run_in_background, args=(run_id,), daemon=True).start()


def load_run(run_id: str) -> dict:
    if not NAME.fullmatch(run_id) or not metadata_path(run_id).is_file():
        raise FileNotFoundError(run_id)
    data = read_json_file(metadata_path(run_id))
    process = PROCESSES.get(run_id)
    if data.get("executionTarget") == "hpc":
        if data.get("status") in {"queued", "running"}:
            schedule_hpc_refresh(run_id)
    elif process and process.poll() is not None and data["status"] == "running":
        data["status"] = "completed" if process.returncode == 0 else "failed"
        data["exitCode"] = process.returncode
        data["finishedAt"] = now()
        atomic_json(metadata_path(run_id), data)
    elif not process and data.get("status") == "running" and data.get("pid"):
        try:
            os.kill(int(data["pid"]), 0)
        except (OSError, ValueError):
            detected = detect_stage(Path(data["logFile"]), Path(data["runRoot"]) / ".pipeline-stage")
            data["status"] = "completed" if detected == "summary" else "interrupted"
            data["stage"] = detected
            data["finishedAt"] = now()
            data["exitCode"] = 0 if detected == "summary" else None
            atomic_json(metadata_path(run_id), data)
    detected_stage = detect_stage(Path(data["logFile"]), Path(data["runRoot"]) / ".pipeline-stage")
    if data.get("stage") != detected_stage:
        data["stage"] = detected_stage
        data["stageStartedAt"] = now()
        atomic_json(metadata_path(run_id), data)
    return data


def detect_stage(log: Path, marker: Path | None = None) -> str:
    if marker and marker.is_file():
        text = marker.read_text(encoding="utf-8", errors="replace")
    elif log.is_file():
        with log.open("rb") as handle:
            size = log.stat().st_size
            handle.seek(max(0, size - 10_000_000))
            text = handle.read().decode(errors="replace")
    else:
        return "queued"
    headings = re.findall(r"Stage: ([^\n]+)", text)
    if not headings:
        return "starting"
    label = headings[-1].lower()
    aliases = {"baseline csv to model inputs": "inputs", "openrewrite": "rewrite",
               "focused tests": "focused_test", "format openrewrite changes": "format",
               "final verification": "final_verify", "smell comparison": "smells"}
    return aliases.get(label, label.replace(" ", "_"))


def safe_upload(folder: Path, item: dict, expected: str) -> Path:
    if item.get("name") != expected or not isinstance(item.get("data"), str):
        raise ValueError(f"Expected uploaded file {expected}")
    raw = base64.b64decode(item["data"], validate=True)
    if len(raw) > 50 * 1024 * 1024:
        raise ValueError(f"{expected} exceeds 50 MB")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / expected
    path.write_bytes(raw)
    return path


def validate_csv_inputs(folder: Path, system: str, version: str) -> None:
    for name in ("component-metrics.csv", "smell-characteristics.csv", "smell-affects.csv"):
        path = folder / name
        if not path.is_file():
            raise ValueError(f"Missing {name}")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise ValueError(f"{name} is empty")
        projects = {row.get("project") for row in rows if row.get("project")}
        versions = {row.get("versionId") for row in rows if row.get("versionId")}
        if projects and projects != {system}:
            raise ValueError(f"{name}: project {sorted(projects)} does not equal {system}")
        if versions and versions != {version}:
            raise ValueError(f"{name}: versionId does not equal {version}")


def latest_compatible_predictions(system: str, version: str, exclude: str) -> Path:
    shared = PIPELINE_CACHE / system / version.lower() / "predictions.csv"
    if shared.is_file():
        return shared
    candidates: list[tuple[str, Path]] = []
    for metadata in RUNS.glob("*/metadata.json"):
        if metadata.parent.name == exclude:
            continue
        try:
            data = read_json_file(metadata)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("system") != system or data.get("versionId") != version:
            continue
        path = Path(data.get("runRoot", "")) / "pipeline-results" / f"{system}_refactoring_suggestions_from_trained_model.csv"
        if path.is_file():
            candidates.append((str(data.get("createdAt", "")), path))
    if not candidates:
        raise ValueError("No compatible prediction CSV exists; run the full workflow first")
    return max(candidates, key=lambda item: item[0])[1]


def baseline_for_resume(run_id: str, system: str, version: str) -> Path:
    for metadata in RUNS.glob("*/metadata.json"):
        try:
            data = read_json_file(metadata)
        except (OSError, json.JSONDecodeError):
            continue
        if (str(data.get("logicalRunId", "")) != run_id or data.get("system") != system
                or data.get("versionId") != version):
            continue
        candidate = metadata.parent / "inputs" / "baseline"
        if all((candidate / name).is_file() for name in
               ("component-metrics.csv", "smell-characteristics.csv", "smell-affects.csv")):
            return candidate
    return ROOT / "baseline_csv"


def cache_completed_artifacts(data: dict) -> None:
    """Retain reusable, target-compatible outputs outside disposable run folders."""
    run_root = Path(data["runRoot"])
    cache = PIPELINE_CACHE / data["system"] / data["versionId"].lower()
    prediction = run_root / "pipeline-results" / f"{data['system']}_refactoring_suggestions_from_trained_model.csv"
    if prediction.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prediction, cache / "predictions.csv")
        provenance = {
            "system": data["system"], "versionId": data["versionId"],
            "repositoryUrl": data["repositoryUrl"], "sourceRun": data["id"],
            "cachedAt": now(),
        }
        atomic_json(cache / "provenance.json", provenance)
        for name in ("model-evaluation.json", "training-data-quality.json"):
            source = run_root / "results" / name
            if source.is_file():
                shutil.copy2(source, cache / name)


def monitor(run_id: str, process: subprocess.Popen[str], handle) -> None:
    code = process.wait()
    handle.close()
    with LOCK:
        data = read_json_file(metadata_path(run_id))
        if data["status"] != "stopped":
            data["status"] = "completed" if code == 0 else "failed"
        data["exitCode"] = code
        data["finishedAt"] = now()
        if code == 0:
            cache_completed_artifacts(data)
        atomic_json(metadata_path(run_id), data)
        PROCESSES.pop(run_id, None)


def validate_identity(payload: dict) -> tuple[str, str, str]:
    system = str(payload.get("system", "")).strip()
    repository = str(payload.get("repositoryUrl", "")).strip()
    version = str(payload.get("versionId", "")).strip()
    if not NAME.fullmatch(system):
        raise ValueError("System must contain only letters, numbers, dot, dash or underscore")
    if not repository.startswith(("https://", "http://", "git@", "file://", "/")):
        raise ValueError("Repository URL is invalid")
    if not COMMIT.fullmatch(version):
        raise ValueError("Version ID must be a Git commit hash")
    return system, repository, version


def validate_batch_options(payload: dict) -> tuple[list[str], int, int, int]:
    categories = payload.get("severityCategories", ["high", "medium", "low"])
    if (not isinstance(categories, list) or not categories
            or not set(categories).issubset({"high", "medium", "low"})):
        raise ValueError("Select one or more valid severity categories")
    try:
        batch_size = int(payload.get("batchSize", 10))
        start_batch = int(payload.get("startBatch", 1))
        max_batches = int(payload.get("maxBatches", 0))
    except (TypeError, ValueError) as error:
        raise ValueError("Batch size and maximum batches must be integers") from error
    if not 1 <= batch_size <= 100 or start_batch < 1 or max_batches < 0:
        raise ValueError("Batch size must be 1-100, start batch positive, and maximum batches nonnegative")
    return categories, batch_size, start_batch, max_batches


def submission_key(payload: dict) -> str:
    fields = {key: payload.get(key) for key in (
        "system", "repositoryUrl", "versionId", "executionTarget", "mode",
        "freshMining", "allowRiskyCandidates", "severityCategories", "batchSize",
        "startBatch", "maxBatches", "resumeRunId", "resumeStage",
    )}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def recent_active_submission(key: str) -> dict | None:
    cutoff = datetime.now(timezone.utc).timestamp() - 120
    for metadata in RUNS.glob("*/metadata.json"):
        try:
            data = read_json_file(metadata)
            created = datetime.fromisoformat(data["createdAt"]).timestamp()
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
        if data.get("submissionKey") == key and data.get("status") in {"queued", "running"} and created >= cutoff:
            return data
    return None


def start_hpc_run(payload: dict) -> dict:
    if EXECUTION_MODE not in {"hpc", "both"}:
        raise ValueError("This dashboard was not started with HPC execution enabled")
    if not hpc_available():
        raise ValueError("Slurm commands or hpc/noctua_pipeline.sbatch are unavailable")
    system, repository, version = validate_identity(payload)
    key = submission_key(payload)
    duplicate = recent_active_submission(key)
    if duplicate:
        return duplicate
    categories, batch_size, start_batch, max_batches = validate_batch_options(payload)
    mode = str(payload.get("mode", "latest_predictions"))
    fresh_mining = bool(payload.get("freshMining", False))
    allow_risky = bool(payload.get("allowRiskyCandidates", False))
    resume_id = str(payload.get("resumeRunId", "")).strip()
    resume_stage = str(payload.get("resumeStage", "")).strip()
    if bool(resume_id) != bool(resume_stage):
        raise ValueError("Resume run ID and resume stage must be provided together")
    if resume_id and (not NAME.fullmatch(resume_id) or resume_stage not in RESUME_STAGES):
        raise ValueError("Invalid HPC run ID or resume stage")

    pending_id = "hpc-pending-" + uuid.uuid4().hex[:10]
    folder = RUNS / pending_id
    inputs = folder / "inputs"
    uploads = payload.get("baselineFiles") or []
    by_name = {item.get("name"): item for item in uploads if isinstance(item, dict)}
    if resume_id:
        baseline = baseline_for_resume(resume_id, system, version)
    else:
        for filename in ("component-metrics.csv", "smell-characteristics.csv", "smell-affects.csv"):
            safe_upload(inputs / "baseline", by_name.get(filename, {}), filename)
        validate_csv_inputs(inputs / "baseline", system, version)
        baseline = inputs / "baseline"

    pipeline_mode = "train"
    predictions = ""
    training = ""
    extra = payload.get("extraFile")
    if mode == "latest_predictions":
        predictions = str(latest_compatible_predictions(system, version, pending_id))
        pipeline_mode = "reuse_predictions"
    elif mode == "predictions":
        predictions = str(safe_upload(inputs, extra or {}, "predictions.csv"))
        pipeline_mode = "reuse_predictions"
    elif mode == "training":
        training = str(safe_upload(inputs, extra or {}, "training.jsonl"))
        pipeline_mode = "training"
    elif mode == "full":
        pipeline_mode = "fresh" if fresh_mining else "train"
    else:
        raise ValueError("Unknown run mode")

    folder.mkdir(parents=True, exist_ok=True)
    slurm_log_pattern = STATE / "slurm-%j.log"
    environment = os.environ.copy()
    environment.update({
        "PROJECT_SPACE": str(HPC_PROJECT_SPACE), "PROJECT_ROOT": str(ROOT),
        "SYSTEM": system, "REPOSITORY_URL": repository, "VERSION_ID": version,
        "BASELINE_CSV_DIR": str(baseline), "PIPELINE_MODE": pipeline_mode,
        "PREDICTIONS_CSV": predictions, "TRAINING_DATASET": training,
        "SEVERITY_CATEGORIES": ",".join(categories), "BATCH_SIZE": str(batch_size),
        "START_BATCH": str(start_batch), "MAX_BATCHES": str(max_batches),
        "ALLOW_RISKY_CANDIDATES": "1" if allow_risky else "0",
        "PROFILE": "log4j2" if system == "logging-log4j2" else "generic",
    })
    if resume_id:
        environment.update({"RESUME_RUN_ID": resume_id, "START_STAGE": resume_stage})
    command = ["sbatch", "--parsable", f"--output={slurm_log_pattern}", f"--error={slurm_log_pattern}",
               "--export=ALL", str(HPC_SCRIPT)]
    submitted = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                               text=True, timeout=30, check=False)
    if submitted.returncode != 0:
        raise RuntimeError(f"Slurm submission failed: {submitted.stderr.strip() or submitted.stdout.strip()}")
    job_id = submitted.stdout.strip().split(";", 1)[0]
    if not job_id.isdigit():
        raise RuntimeError(f"Unexpected sbatch response: {submitted.stdout.strip()}")
    # Keep the pre-submission folder name: its absolute input paths were already
    # exported to Slurm and must remain stable even if the job starts instantly.
    run_id = pending_id
    log = STATE / f"slurm-{job_id}.log"
    run_root = HPC_RUNS_ROOT / system / (resume_id or job_id)
    meta = {
        "id": run_id, "system": system, "repositoryUrl": repository, "versionId": version,
        "mode": mode, "executionTarget": "hpc", "freshMining": fresh_mining,
        "allowRiskyCandidates": allow_risky, "severityCategories": categories,
        "batchSize": batch_size, "startBatch": start_batch, "maxBatches": max_batches,
        "status": "queued", "stage": "queued", "createdAt": now(), "stageStartedAt": now(),
        "finishedAt": None, "exitCode": None, "command": command, "runRoot": str(run_root),
        "logFile": str(log), "slurmJobId": job_id, "logicalRunId": resume_id or job_id,
        "resumeRunId": resume_id or None, "resumeStage": resume_stage or None,
        "submissionKey": key,
    }
    atomic_json(metadata_path(run_id), meta)
    return meta


def start_run(payload: dict) -> dict:
    if payload.get("executionTarget") == "hpc":
        return start_hpc_run(payload)
    system, repository, version = validate_identity(payload)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    folder = RUNS / run_id
    inputs = folder / "inputs"
    run_root = folder / "workspace"
    uploads = payload.get("baselineFiles") or []
    by_name = {item.get("name"): item for item in uploads if isinstance(item, dict)}
    for filename in ("component-metrics.csv", "smell-characteristics.csv", "smell-affects.csv"):
        safe_upload(inputs / "baseline", by_name.get(filename, {}), filename)
    validate_csv_inputs(inputs / "baseline", system, version)

    command = [str(PIPELINE), "--system", system, "--repository-url", repository,
               "--version-id", version, "--baseline-csv-dir", str(inputs / "baseline"),
               "--run-root", str(run_root)]
    if system == "logging-log4j2":
        command += ["--profile", "log4j2"]
    mode = payload.get("mode", "full")
    extra = payload.get("extraFile")
    if mode == "predictions":
        path = safe_upload(inputs, extra or {}, "predictions.csv")
        command += ["--predictions-csv", str(path)]
    elif mode == "training":
        path = safe_upload(inputs, extra or {}, "training.jsonl")
        command += ["--training-dataset", str(path)]
    elif mode == "latest_predictions":
        path = latest_compatible_predictions(system, version, run_id)
        command += ["--predictions-csv", str(path)]
    elif mode != "full":
        raise ValueError("Unknown run mode")
    fresh_mining = bool(payload.get("freshMining", False))
    allow_risky_candidates = bool(payload.get("allowRiskyCandidates", False))
    severity_categories, batch_size, start_batch, max_batches = validate_batch_options(payload)
    if fresh_mining:
        if mode != "full":
            raise ValueError("Fresh mining is available only for the full workflow")
        command.append("--remine")
    if allow_risky_candidates:
        command.append("--allow-risky-candidates")
    command += ["--severity-categories", ",".join(severity_categories),
                "--batch-size", str(batch_size), "--start-batch", str(start_batch),
                "--max-batches", str(max_batches)]

    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "pipeline.log"
    meta = {"id": run_id, "system": system, "repositoryUrl": repository, "versionId": version,
            "mode": mode, "executionTarget": "local", "freshMining": fresh_mining,
            "allowRiskyCandidates": allow_risky_candidates,
            "severityCategories": severity_categories, "batchSize": batch_size,
            "startBatch": start_batch, "maxBatches": max_batches,
            "status": "running", "stage": "starting", "createdAt": now(), "stageStartedAt": now(),
            "finishedAt": None, "exitCode": None, "command": command, "runRoot": str(run_root),
            "logFile": str(log)}
    atomic_json(metadata_path(run_id), meta)
    handle = log.open("w", encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                               text=True, start_new_session=True, env=environment)
    meta["pid"] = process.pid
    atomic_json(metadata_path(run_id), meta)
    with LOCK:
        PROCESSES[run_id] = process
    threading.Thread(target=monitor, args=(run_id, process, handle), daemon=True).start()
    return meta


def result_summary(data: dict) -> dict:
    result = Path(data["runRoot"]) / "results"
    output: dict = {"available": result.is_dir()}
    for key, relative in {
        "manifest": "generated-openrewrite/manifest.json",
        "validation": "openrewrite-validation/validation-report.json",
        "comparison": "arcan-comparison.json",
        "arcan": "arcan-refactored/summary.json",
        "experiment": "experiment-report.json",
        "modelEvaluation": "model-evaluation.json",
        "trainingQuality": "training-data-quality.json",
    }.items():
        path = result / relative
        if path.is_file():
            output[key] = json.loads(path.read_text(encoding="utf-8"))
    predictions = Path(data["runRoot"]) / "pipeline-results" / f"{data['system']}_refactoring_suggestions_from_trained_model.csv"
    prediction_origin = "generated_or_archived_in_run"
    if not predictions.is_file():
        command = data.get("command") or []
        try:
            predictions = Path(command[command.index("--predictions-csv") + 1])
            prediction_origin = "reused_from_compatible_run"
        except (ValueError, IndexError, TypeError):
            pass
    if not predictions.is_file():
        predictions = PIPELINE_CACHE / data["system"] / data["versionId"].lower() / "predictions.csv"
        prediction_origin = "shared_compatible_cache"
    if predictions.is_file():
        with predictions.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        output["predictions"] = {
            "count": len(rows), "sample": rows[:25], "origin": prediction_origin,
            "source": str(predictions),
        }
    expected_artifacts = [
        f"pipeline-results/{data['system']}_refactoring_suggestions_from_trained_model.csv",
        "results/baseline-input-validation.json", "results/run-provenance.json",
        "results/training-data-quality.json", "results/model-evaluation.json",
        "results/generated-openrewrite/manifest.csv",
        "results/openrewrite-validation/validation-report.csv",
        "results/arcan-comparison.csv", "results/arcan-comparison.json",
        "results/experiment-report.json", "results/arcan-refactored/summary.json",
    ]
    run_root = Path(data["runRoot"])
    output["artifacts"] = [
        {"path": relative, "name": Path(relative).name, "size": (run_root / relative).stat().st_size}
        for relative in expected_artifacts if (run_root / relative).is_file()
    ]
    return output


class Handler(BaseHTTPRequestHandler):
    server_version = "RefactoringDashboard/2.0"

    def json_response(self, value: object, status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 160 * 1024 * 1024:
            raise ValueError("Request exceeds 160 MB")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path); parts = [unquote(x) for x in parsed.path.split("/") if x]
        try:
            if parsed.path == "/api/health":
                return self.json_response({"ok": True, "apiVersion": 3,
                                           "executionMode": EXECUTION_MODE,
                                           "hpcAvailable": hpc_available(),
                                           "hpcProjectSpace": str(HPC_PROJECT_SPACE)})
            if parsed.path == "/api/runs":
                values = []
                for metadata in RUNS.glob("*/metadata.json"):
                    try:
                        values.append(load_run(metadata.parent.name))
                    except (OSError, ValueError, json.JSONDecodeError):
                        # Preserve the damaged file for diagnosis, but do not let
                        # one historical experiment disconnect every UI user.
                        continue
                return self.json_response(sorted(values, key=lambda x: x["createdAt"], reverse=True))
            if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                data = load_run(parts[2])
                if len(parts) == 3: return self.json_response(data)
                if parts[3] == "log":
                    query = parse_qs(parsed.query); offset = max(0, int(query.get("offset", [0])[0]))
                    path = Path(data["logFile"]); raw = path.read_bytes() if path.is_file() else b""
                    reset = offset > len(raw)
                    if reset:
                        offset = 0
                    return self.json_response({
                        "offset": len(raw), "text": raw[offset:].decode(errors="replace"), "reset": reset,
                    })
                if parts[3] == "results": return self.json_response(result_summary(data))
                if parts[3] == "download":
                    relative = "/".join(parts[4:]); root = Path(data["runRoot"]).resolve()
                    target = (root / relative).resolve()
                    if root not in target.parents or not target.is_file(): raise FileNotFoundError(relative)
                    body = target.read_bytes(); self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); return self.wfile.write(body)
            asset = ASSETS / ("index.html" if parsed.path == "/" else parsed.path.lstrip("/"))
            if asset.is_file() and ASSETS.resolve() in asset.resolve().parents:
                body = asset.read_bytes(); self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(asset.name)[0] or "text/plain")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); return self.wfile.write(body)
            self.send_error(404)
        except FileNotFoundError: self.json_response({"error": "Not found"}, 404)
        except Exception as exc: self.json_response({"error": str(exc)}, 400)

    def do_POST(self) -> None:
        parts = [x for x in urlparse(self.path).path.split("/") if x]
        try:
            if parts == ["api", "runs"]:
                with SUBMIT_LOCK:
                    return self.json_response(start_run(self.read_json()), 201)
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "stop":
                data = load_run(parts[2]); process = PROCESSES.get(parts[2])
                if data.get("executionTarget") == "hpc" and data.get("status") in {"queued", "running"}:
                    stopped = subprocess.run(["scancel", str(data["slurmJobId"])], capture_output=True,
                                             text=True, timeout=15, check=False)
                    if stopped.returncode != 0:
                        raise RuntimeError(stopped.stderr.strip() or "Unable to cancel Slurm job")
                elif process and process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                data["status"] = "stopped"; data["finishedAt"] = now(); atomic_json(metadata_path(parts[2]), data)
                return self.json_response(data)
            self.send_error(404)
        except Exception as exc: self.json_response({"error": str(exc)}, 400)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main() -> None:
    global EXECUTION_MODE, HPC_PROJECT_SPACE, HPC_RUNS_ROOT
    parser = argparse.ArgumentParser(); parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--execution-mode", choices=("local", "hpc", "both"), default="local")
    parser.add_argument("--hpc-project-space", type=Path, default=HPC_PROJECT_SPACE)
    args = parser.parse_args()
    EXECUTION_MODE = args.execution_mode
    HPC_PROJECT_SPACE = args.hpc_project_space.resolve()
    HPC_RUNS_ROOT = HPC_PROJECT_SPACE / "runs"
    if EXECUTION_MODE in {"hpc", "both"} and not hpc_available():
        parser.error("HPC mode requires sbatch, squeue, sacct, scancel and hpc/noctua_pipeline.sbatch")
    STATE.mkdir(parents=True, exist_ok=True); RUNS.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"Dashboard: http://{args.host}:{args.port} ({EXECUTION_MODE} execution)")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__":
    main()
