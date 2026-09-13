"""Local research workspace; explicit jobs invoke the existing offline CLI.

This authoring tool is separate from the read-only demo API in /api. Opening or
refreshing it never fetches weather or trains a model. Bind to loopback only.
Run: python -m pipeline.workbench
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import secrets
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from subprocess import run as run_command
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from pipeline.common import ROOT, fingerprint, read_hourly, write_json

APP_ID = "headroom-ml-workspace"
ASSETS = Path(__file__).with_name("workbench_assets")
DOWNLOADS = {"REPORT.md", "model_card.json", "model.joblib", "test_predictions.parquet", "confidence.json",
             "hours_summary.json", "monthly_expected_hours.csv", "exposure_by_location.parquet",
             "simulation_metadata.json", "contract_hours.json"}
SITE_DOWNLOADS = {"site_report.json", "SITE_REPORT.md", "SITE_REPORT.html"}


class Workspace:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.processed = self.root / "data/processed"
        self.home = self.processed / "workbench"
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.job: dict | None = None

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def resolve(self, identifier: str) -> Path:
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Select a local dataset or model run.")
        path = (self.root / identifier).resolve()
        if not path.is_relative_to(self.processed.resolve()):
            raise ValueError("Only files in this repository's processed-data folder are available.")
        return path

    def datasets(self) -> list[dict]:
        rows = []
        # Only prepared hourly inputs, never raw downloads or synthetic fixtures.
        folders = [self.processed / "ml_inputs", self.home / "inputs"]
        for folder in folders:
            for path in sorted(folder.glob("*.parquet")):
                try:
                    frame = read_hourly(self.resolve(self.relative(path)))
                    weather_path = path.with_suffix(".weather.json")
                    source = json.loads(weather_path.read_text(encoding="utf-8")) if weather_path.exists() else {}
                    area = source.get("location_mapping", {}).get("area_input", "")
                    rows.append({"id": self.relative(path), "name": path.stem, "area": area,
                                 "hours": len(frame), "locations": sorted(frame.location_id.unique()),
                                 "temperature_hours": int(frame.temperature_c.notna().sum()) if "temperature_c" in frame else 0,
                                 "event_hours": int(frame.event_active.notna().sum()) if "event_active" in frame else 0})
                except (ValueError, OSError, KeyError):
                    continue  # A partially written job is not a completed dataset.
        return rows

    def dataset_path(self, identifier: str) -> Path:
        if identifier not in {item["id"] for item in self.datasets()}:
            raise ValueError("Dataset is not available. Refresh and select a prepared hourly dataset.")
        return self.resolve(identifier)

    def runs(self) -> list[dict]:
        rows = []
        for path in sorted(self.processed.rglob("model_card.json"), reverse=True):
            if not path.resolve().is_relative_to(self.processed.resolve()):
                continue
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
                if not all((path.parent / name).is_file() for name in ("model.joblib", "test_predictions.parquet", "REPORT.md")):
                    continue
                rows.append({"id": self.relative(path.parent), "name": path.parent.name,
                             "model_version": card["model_version"], "status": card["status"]})
            except (ValueError, OSError, KeyError):
                continue
        return rows

    def snapshot(self) -> dict:
        with self.lock:
            job = self.job.copy() if self.job else None
        if job:
            log = self.resolve(job["log_path"])
            job["log"] = log.read_text(encoding="utf-8", errors="replace")[-20000:] if log.exists() else "Starting offline job..."
        return {"app": APP_ID, "datasets": self.datasets(), "runs": self.runs(), "job": job,
                "site_scans": self.site_records("scan.json"), "site_reports": self.site_records("site_report.json")}

    def site_records(self, filename: str) -> list[dict]:
        rows = []
        for path in sorted((self.home / "sites").glob("*/" + filename), reverse=True):
            try:
                data = json.loads(self.resolve(self.relative(path)).read_text(encoding="utf-8"))
                name = data["query"] if filename == "scan.json" else data["location"]["name"]
                rows.append({"id": self.relative(path.parent), "name": name})
            except (ValueError, OSError, KeyError):
                continue
        return rows

    def site_record(self, identifier: str, filename: str) -> dict:
        if identifier not in {row["id"] for row in self.site_records(filename)}:
            raise ValueError("Select an available location search or report.")
        data = json.loads(self.resolve(identifier + "/" + filename).read_text(encoding="utf-8"))
        if filename == "site_report.json":
            data["markdown"] = self.resolve(identifier + "/SITE_REPORT.md").read_text(encoding="utf-8")
        return data

    def dataset(self, identifier: str, location: str) -> dict:
        path = self.dataset_path(identifier)
        frame = read_hourly(path)
        locations = sorted(frame.location_id.unique())
        location = location or locations[0]
        if location not in locations:
            raise ValueError("Select an existing grid location.")
        frame = frame[frame.location_id.eq(location)]
        signals = [name for name in ("load_mw", "temperature_c", "event_active") if name in frame]
        daily = frame.set_index("timestamp_utc")[signals].resample("D").mean().reset_index()
        summary = {"hours": len(frame), "load_hours": int(frame.load_mw.notna().sum()),
                   "temperature_hours": int(frame.temperature_c.notna().sum()) if "temperature_c" in frame else 0,
                   "event_hours": int(frame.event_active.notna().sum()) if "event_active" in frame else 0,
                   "start": str(frame.timestamp_utc.min()), "end": str(frame.timestamp_utc.max())}
        sources = {}
        for suffix in (".weather.json", ".quality.json", ".evidence.json", ".generation.json"):
            source_path = path.with_suffix(suffix)
            if source_path.exists():
                sources[suffix] = json.loads(source_path.read_text(encoding="utf-8"))
        return {"id": identifier, "location": location, "summary": summary, "source_ref": identifier,
                "daily": json.loads(daily.to_json(orient="records", date_format="iso")), "sources": sources}

    def run(self, identifier: str) -> dict:
        if identifier not in {item["id"] for item in self.runs()}:
            raise ValueError("Model run is not available.")
        path = self.resolve(identifier)
        card = json.loads((path / "model_card.json").read_text(encoding="utf-8"))
        hours_path = path / "hours_summary.json"
        hours = json.loads(hours_path.read_text(encoding="utf-8")) if hours_path.exists() else None
        annual = None
        if (path / "exposure_by_location.parquet").exists() and (path / "simulation_metadata.json").exists():
            from pipeline.simulate import get_location_estimate
            locations = pd.read_parquet(path / "exposure_by_location.parquet").location_id.unique()
            annual = {"locations": {location: get_location_estimate(location, path=path / "exposure_by_location.parquet") for location in locations},
                      "metadata": json.loads((path / "simulation_metadata.json").read_text(encoding="utf-8"))}
            if (path / "contract_hours.json").exists():
                annual["contract"] = json.loads((path / "contract_hours.json").read_text(encoding="utf-8"))
        return {"id": identifier, "card": card, "report": (path / "REPORT.md").read_text(encoding="utf-8"),
                "hours": hours, "annual": annual,
                "downloads": sorted(name for name in DOWNLOADS if (path / name).is_file())}

    def submit(self, data: dict) -> dict:
        # One research job at a time prevents duplicate downloads and competing fits.
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise ValueError("An offline job is already running. Wait for it to finish.")
            kind = data.get("kind")
            source = self.dataset_path(data.get("dataset", "")) if kind in {"weather", "train"} else None
            identifier = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
            job_dir = self.home / "jobs" / identifier
            commands = []
            if kind in {"site-scan", "site-report", "site-transfer", "site-signals"}:
                from pipeline.site import validate_query, validate_assumptions
                if kind == "site-scan":
                    request = {"kind": kind, "query": validate_query(data.get("query"))}
                elif kind == "site-signals":
                    saved_id = data.get("report", "")
                    saved = self.site_record(saved_id, "site_report.json")
                    if saved.get("status") != "research_modeled_exposure":
                        raise ValueError("Select a completed system report to explain, or generate a regional comparison.")
                    self.run(saved["model_run"])
                    request = {"kind": kind, "source_dir": str(self.resolve(saved_id))}
                else:
                    scan = self.site_record(data.get("scan", ""), "scan.json")
                    index = data.get("candidate")
                    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(scan["candidates"]):
                        raise ValueError("Choose a result from your location search.")
                    request = {"kind": kind, "scan": scan, "point": scan["candidates"][index], **validate_assumptions(data)}
                job_dir.mkdir(parents=True)
                request_path = job_dir / "site_request.json"
                write_json(request_path, request)
                output = self.home / "sites" / identifier
                commands = [["site-job", "--request", str(request_path), "--out", str(output)]]
                result_id = self.relative(output)
            elif kind == "hours":
                run_id = data.get("run", "")
                saved = self.run(run_id)
                run_dir = self.resolve(run_id)
                years = data.get("years", 0)
                if isinstance(years, bool) or not isinstance(years, int) or not 0 <= years <= 7:
                    raise ValueError("Choose zero for the scored period, or 1–7 years for annual simulation.")
                commands = [["hours", "--run-dir", str(run_dir)]]
                if years:
                    if not saved["hours"]:
                        raise ValueError("Calculate scored-period hours before requesting annual simulation.")
                    blockers = [reason for info in saved["hours"]["locations"].values() for reason in info["annual_blockers"]]
                    if blockers:
                        raise ValueError("Annual simulation needs more held-out history. " + blockers[0])
                    commands.append(["simulate", "--run-dir", str(run_dir), "--years", str(years)])
                result_id = run_id
            elif kind == "weather":
                area = data.get("area", "")
                if not isinstance(area, str) or not 2 <= len(area.strip()) <= 120 or any(ord(c) < 32 for c in area):
                    raise ValueError('Enter a US city and state, for example "Amarillo, TX".')
                frame = read_hourly(source)
                if any(column.startswith("temperature_") for column in frame):
                    raise ValueError("Choose a dataset without temperature data to prepare another area.")
                location = data.get("location", "")
                if location not in set(frame.location_id):
                    raise ValueError("Choose an existing grid location.")
                output = self.home / "inputs" / f"weather_{identifier}.parquet"
                commands.append(["add-temperature", "--hourly", str(source), "--area", area.strip(),
                                 "--location-id", location, "--out", str(output)])
                result_id = self.relative(output)
            elif kind == "train":
                csv = data.get("evidence_csv", "")
                label_ref = data.get("label_ref", "")
                if not isinstance(label_ref, str) or not 5 <= len(label_ref.strip()) <= 2000:
                    raise ValueError("Provide the source and coverage reference for your reviewed event labels.")
                if not isinstance(csv, str) or not csv.strip():
                    raise ValueError("Choose a reviewed event CSV with confirmed 0/1 labels.")
                if len(csv.encode("utf-8")) > 5_000_000:
                    raise ValueError("The event CSV must be smaller than 5 MB.")
                evidence = pd.read_csv(io.StringIO(csv))
                required = {"timestamp_utc", "location_id", "event_active"}
                if set(evidence.columns) != required:
                    raise ValueError("Event CSV must contain exactly timestamp_utc, location_id, event_active.")
                # Validate before creating any files. Blank labels remain unknown.
                from pipeline.label import label_hours
                evidence.event_active = pd.to_numeric(evidence.event_active, errors="raise")
                label_hours(evidence, {"label_method": "observed_event", "label_ref": label_ref.strip()})
                if "event_active" in read_hourly(source):
                    raise ValueError("Choose the input before its event-evidence join; existing labels cannot be overwritten.")
                job_dir.mkdir(parents=True)
                evidence_path = job_dir / "reviewed_events.csv"
                evidence_path.write_text(csv, encoding="utf-8")
                output = self.home / "inputs" / f"labeled_{identifier}.parquet"
                policy_path = job_dir / "policy.json"
                policy = {"operator": "SPP", "label_method": "observed_event", "label_ref": label_ref.strip(),
                          "data_ref": f"{self.relative(source)}; sha256={fingerprint(source)}; events_sha256={fingerprint(evidence_path)}"}
                write_json(policy_path, policy)
                run_dir = self.home / "runs" / identifier
                commands = [["join-evidence", "--hourly", str(source), "--evidence", str(evidence_path), "--out", str(output)],
                            ["train", "--hourly", str(output), "--policy", str(policy_path), "--run-dir", str(run_dir)]]
                result_id = self.relative(run_dir)
            else:
                raise ValueError("Unknown offline job type.")
            job_dir.mkdir(parents=True, exist_ok=True)
            self.job = {"id": identifier, "kind": kind, "status": "running", "result_id": result_id,
                        "log_path": self.relative(job_dir / "output.log")}
            job = self.job.copy()
            write_json(job_dir / "job.json", job)
            threading.Thread(target=self._execute, args=(commands, job), daemon=True).start()
            return job

    def _execute(self, commands: list[list[str]], job: dict) -> None:
        log_path = self.resolve(job["log_path"])
        status = "succeeded"
        try:
            with log_path.open("w", encoding="utf-8") as log:
                for arguments in commands:
                    completed = run_command([sys.executable, "-u", "-m", "pipeline.workflow", *arguments],
                                               cwd=self.root, stdout=log, stderr=subprocess.STDOUT, shell=False,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if completed.returncode:
                        status = "failed"
                        break
        except Exception as error:
            status = "failed"
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"Could not run the offline pipeline: {error}\n")
        with self.lock:
            job["status"] = status
            self.job = job
            write_json(log_path.with_name("job.json"), job)


def handler_for(workspace: Workspace):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, payload: bytes | dict, status=200, content_type="application/json", filename=None):
            if isinstance(payload, dict):
                payload = json.dumps(payload, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(payload)

        def local_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def do_GET(self):
            if not self.local_host():
                return self.respond({"error": "Use the local workspace URL."}, 403)
            request = urlsplit(self.path)
            query = {key: values[0] for key, values in parse_qs(request.query).items()}
            try:
                if request.path == "/health":
                    return self.respond({"app": APP_ID})
                if request.path == "/":
                    html = (ASSETS / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", workspace.token)
                    return self.respond(html.encode("utf-8"), content_type="text/html; charset=utf-8")
                if request.path in {"/app.js", "/style.css"}:
                    path = ASSETS / request.path[1:]
                    return self.respond(path.read_bytes(), content_type="text/javascript" if path.suffix == ".js" else "text/css")
                if request.path.startswith("/fonts/"):
                    name = request.path.removeprefix("/fonts/")
                    if name not in {"ibm-plex-sans-400.ttf", "ibm-plex-sans-600.ttf", "ibm-plex-mono-400.ttf"}:
                        raise ValueError("Font not available.")
                    return self.respond((workspace.root / "web/public/fonts" / name).read_bytes(), content_type="font/ttf")
                if request.path == "/api/state":
                    return self.respond(workspace.snapshot())
                if request.path == "/api/dataset":
                    return self.respond(workspace.dataset(query.get("id", ""), query.get("location", "")))
                if request.path == "/api/run":
                    return self.respond(workspace.run(query.get("id", "")))
                if request.path in {"/api/site-scan", "/api/site-report"}:
                    filename = "scan.json" if request.path.endswith("site-scan") else "site_report.json"
                    return self.respond(workspace.site_record(query.get("id", ""), filename))
                if request.path == "/api/site-download":
                    identifier, name = query.get("id", ""), query.get("file", "")
                    workspace.site_record(identifier, "site_report.json")
                    if name not in SITE_DOWNLOADS:
                        raise ValueError("File is not a downloadable site report.")
                    return self.respond(workspace.resolve(identifier + "/" + name).read_bytes(), content_type="application/octet-stream", filename=name)
                if request.path == "/api/download":
                    identifier, name = query.get("id", ""), query.get("file", "")
                    workspace.run(identifier)
                    if name not in DOWNLOADS:
                        raise ValueError("File is not a downloadable model artifact.")
                    path = workspace.resolve(identifier + "/" + name)
                    return self.respond(path.read_bytes(), content_type="application/octet-stream", filename=name)
                if request.path == "/event-template.csv":
                    return self.respond(b"timestamp_utc,location_id,event_active\n", content_type="text/csv", filename="reviewed_events.csv")
                return self.respond({"error": "Not found."}, 404)
            except (ValueError, OSError, KeyError) as error:
                return self.respond({"error": str(error)}, 400)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.respond({"error": "Invalid request length."}, 400)
            # Consume a bounded body before replying, including rejected requests.
            # Closing with unread bytes can reset the connection on Windows.
            self.connection.settimeout(20)
            try:
                body = self.rfile.read(length) if 0 < length <= 6_000_000 else b""
            except TimeoutError:
                return self.respond({"error": "Request body timed out."}, 408)
            origin = self.headers.get("Origin")
            allowed_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if not self.local_host() or (origin and origin not in allowed_origins) or not secrets.compare_digest(self.headers.get("X-Workspace-Token", ""), workspace.token):
                return self.respond({"error": "Reload the local workspace before starting a job."}, 403)
            if self.path != "/api/jobs":
                return self.respond({"error": "Not found."}, 404)
            try:
                if not 0 < length <= 6_000_000 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Submit a JSON job request under 6 MB.")
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError("Invalid job request.")
                return self.respond(workspace.submit(data), 202)
            except (ValueError, OSError, KeyError) as error:
                return self.respond({"error": str(error)}, 400)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Open Kristian's local ML research workspace.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(Workspace()))
    print(f"Fluxline ML workspace: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
