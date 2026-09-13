"""Local workspace tests; all training uses disposable synthetic fixtures."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from pipeline.workbench import Workspace, handler_for
from pipeline.workflow import main as workflow_main


def fixture(hours=2400):
    import numpy as np
    rng = np.random.default_rng(150)
    temperature = rng.choice([-15.0, 15.0, 40.0], hours)
    return pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-01", periods=hours, freq="h", tz="UTC"),
                         "location_id": "TEST_ONLY", "load_mw": 1000.0, "temperature_c": temperature,
                         "temperature_source_ref": "test://disposable-workspace-fixture"})


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "data/processed/ml_inputs"
        self.inputs.mkdir(parents=True)
        self.hourly = fixture()
        self.input_path = self.inputs / "fixture.parquet"
        self.hourly.to_parquet(self.input_path, index=False)
        self.workspace = Workspace(self.root)
        self.identifier = self.workspace.relative(self.input_path)

    def test_initial_status_and_data_are_real_and_read_only(self):
        with patch("requests.get", side_effect=AssertionError("No fetch when viewing")), patch("pipeline.workbench.run_command", side_effect=AssertionError("No training when viewing")):
            state = self.workspace.snapshot()
            self.assertEqual(state["runs"], [])
            self.assertIsNone(state["job"])
            data = self.workspace.dataset(self.identifier, "TEST_ONLY")
        self.assertEqual(data["summary"]["hours"], 2400)
        self.assertEqual(data["summary"]["temperature_hours"], 2400)
        self.assertEqual(data["summary"]["event_hours"], 0)
        self.assertEqual(data["daily"][0]["load_mw"], 1000)
        self.assertEqual(self.workspace.datasets()[0]["locations"], ["TEST_ONLY"])

    def test_paths_outside_processed_data_and_unknown_locations_are_rejected(self):
        for path in ("../../private.txt", str(self.root / "secret.csv"), "/etc/passwd"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.workspace.resolve(path)
        with self.assertRaisesRegex(ValueError, "grid location"):
            self.workspace.dataset(self.identifier, "WRONG")
        with self.assertRaisesRegex(ValueError, "not available"):
            self.workspace.dataset("data/processed/ml_inputs/unknown.parquet", "TEST_ONLY")

    def test_unsafe_or_missing_labels_fail_without_creating_a_job(self):
        for csv in ("time,label\n2024,1", "timestamp_utc,location_id,event_active\n2024-01-01T00:00:00Z,TEST_ONLY,2"):
            with self.subTest(csv=csv), self.assertRaises(ValueError):
                self.workspace.submit({"kind": "train", "dataset": self.identifier, "evidence_csv": csv, "label_ref": "test://reviewed-fixture"})
        with self.assertRaisesRegex(ValueError, "source and coverage"):
            self.workspace.submit({"kind": "train", "dataset": self.identifier, "evidence_csv": "", "label_ref": ""})
        self.assertFalse(self.workspace.home.exists())

    def test_new_area_requires_original_weather_free_input(self):
        with self.assertRaisesRegex(ValueError, "without temperature"):
            self.workspace.submit({"kind": "weather", "dataset": self.identifier, "area": "Amarillo, TX", "location": "TEST_ONLY"})

    def test_job_uses_argument_list_and_prevents_concurrent_launches(self):
        source = self.hourly[["timestamp_utc", "location_id", "load_mw"]]
        source.to_parquet(self.input_path, index=False)
        with patch("threading.Thread.start"):
            job = self.workspace.submit({"kind": "weather", "dataset": self.identifier, "area": 'City; $(echo unsafe), TX', "location": "TEST_ONLY"})
            with self.assertRaisesRegex(ValueError, "already running"):
                self.workspace.submit({"kind": "weather", "dataset": self.identifier, "area": "Other, TX", "location": "TEST_ONLY"})
        with patch("pipeline.workbench.run_command") as run:
            run.return_value.returncode = 2
            arguments = ["add-temperature", "--area", 'City; $(echo unsafe), TX']
            self.workspace._execute([arguments], job)
            self.assertEqual(run.call_args.args[0][-1], arguments[-1])
            self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(self.workspace.snapshot()["job"]["status"], "failed")

    def test_training_job_produces_accessible_saved_model_and_provenance(self):
        previous = self.hourly.temperature_c.shift(1)
        evidence = self.hourly[["timestamp_utc", "location_id"]].assign(event_active=((previous < 0) | (previous > 30)).astype(int))
        # Run the exact planned offline commands in process for a disposable test
        # root, reducing ensemble size only to keep this software test quick.
        def execute(argv, **kwargs):
            arguments = list(argv[4:])
            if arguments[0] == "train":
                arguments.extend(["--members", "2", "--trees", "25"])
            with patch("sys.argv", ["pipeline.workflow", *arguments]), patch("builtins.print"):
                code = workflow_main()
            return type("Result", (), {"returncode": code})()
        with patch("threading.Thread.start"):
            job = self.workspace.submit({"kind": "train", "dataset": self.identifier,
                                         "evidence_csv": evidence.to_csv(index=False), "label_ref": "test://reviewed-synthetic-events-only"})
        job_dir = self.workspace.resolve(job["log_path"]).parent
        result_dir = self.workspace.resolve(job["result_id"])
        labeled_path = self.workspace.home / "inputs" / f"labeled_{job['id']}.parquet"
        commands = [["join-evidence", "--hourly", str(self.input_path), "--evidence", str(job_dir / "reviewed_events.csv"), "--out", str(labeled_path)],
                    ["train", "--hourly", str(labeled_path), "--policy", str(job_dir / "policy.json"), "--run-dir", str(result_dir)]]
        with patch("pipeline.workbench.run_command", side_effect=execute):
            self.workspace._execute(commands, job)
        self.assertEqual(self.workspace.snapshot()["job"]["status"], "succeeded")
        saved = self.workspace.run(job["result_id"])
        self.assertIn("model.joblib", saved["downloads"])
        self.assertIn("hours_summary.json", saved["downloads"])
        self.assertIsNotNone(saved["hours"])
        self.assertGreater(saved["hours"]["locations"]["TEST_ONLY"]["expected_exposure_hours"], 0)
        self.assertIsNone(saved["annual"])
        with self.assertRaisesRegex(ValueError, "more held-out history"):
            self.workspace.submit({"kind": "hours", "run": job["result_id"], "years": 7})
        with patch("threading.Thread.start"):
            summary_job = self.workspace.submit({"kind": "hours", "run": job["result_id"], "years": 0})
        self.assertEqual(summary_job["kind"], "hours")
        self.assertEqual(saved["card"]["policy"]["label_ref"], "test://reviewed-synthetic-events-only")
        self.assertEqual(saved["card"]["temperature_data"]["source_refs"], ["test://disposable-workspace-fixture"])
        self.assertIn("test_predictions.parquet", saved["downloads"])
        self.assertEqual(saved["card"]["confidence"]["TEST_ONLY"]["level"], "Low")

    def test_http_boundary_requires_local_host_and_token_for_jobs(self):
        class QuietHandler(handler_for(self.workspace)):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(self.workspace.token, response.read().decode())
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        connection.request("POST", "/api/jobs", body="{}", headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.request("GET", "/api/state", headers={"Host": "attacker.invalid"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.request("POST", "/api/jobs", body="{}", headers={"Content-Type": "application/json", "X-Workspace-Token": self.workspace.token, "Origin": "https://attacker.invalid"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.request("GET", "/event-template.csv")
        response = connection.getresponse()
        self.assertEqual(response.read().decode().strip(), "timestamp_utc,location_id,event_active")
        connection.request("POST", "/api/jobs", body=json.dumps({"kind": "invalid", "dataset": self.identifier}), headers={"Content-Type": "application/json", "X-Workspace-Token": self.workspace.token})
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn("Unknown offline job", response.read().decode())


if __name__ == "__main__":
    unittest.main()
