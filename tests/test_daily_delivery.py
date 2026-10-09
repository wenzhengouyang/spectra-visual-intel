import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spectra_agent.daily_delivery import main


class DailyDeliveryTest(unittest.TestCase):
    def test_no_update_does_not_push_alert_or_claim_web_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "daily-20260917"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({
                "status": "completed", "publish_status": "not_published",
            }))
            calls = []

            def step(command):
                calls.append(Path(command[1]).name)
                state = json.loads((run_dir / "run.json").read_text())
                state.update({"publication_mode": "no_update", "no_update": True,
                              "no_update_reason": "今日无更新"})
                (run_dir / "run.json").write_text(json.dumps(state))
                class Result:
                    returncode = 0
                    stdout = '{"status":"no_update"}'
                    stderr = ""
                return Result()

            with patch("spectra_agent.daily_delivery.resolve_config", return_value=(root / "config.json", {})), \
                    patch("spectra_agent.daily_delivery.runs_dir", return_value=root), \
                    patch("spectra_agent.daily_delivery.run_step", side_effect=step), \
                    patch("sys.argv", ["daily_delivery.py", "--run-id", "daily-20260917"]):
                self.assertEqual(main(), 0)
            self.assertEqual(calls, ["publish_run.py"])
            delivery = json.loads((run_dir / "delivery-status.json").read_text())
            self.assertEqual(delivery["web_status"], "no_update")

    def test_web_publish_runs_before_paused_alert(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "daily-20260915"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({
                "status": "completed", "publish_status": "not_published",
            }))
            calls = []

            def step(command):
                calls.append(Path(command[1]).name)
                if Path(command[1]).name == "publish_run.py":
                    state = json.loads((run_dir / "run.json").read_text())
                    state["publish_status"] = "published"
                    (run_dir / "run.json").write_text(json.dumps(state))
                class Result:
                    returncode = 0
                    stdout = '{"status":"paused"}'
                    stderr = ""
                return Result()

            with patch("spectra_agent.daily_delivery.resolve_config", return_value=(root / "config.json", {})), \
                    patch("spectra_agent.daily_delivery.runs_dir", return_value=root), \
                    patch("spectra_agent.daily_delivery.run_step", side_effect=step), \
                    patch("sys.argv", ["daily_delivery.py", "--run-id", "daily-20260915"]):
                self.assertEqual(main(), 0)
            self.assertEqual(calls, ["publish_run.py", "dingtalk_push.py"])

    def test_already_published_run_does_not_publish_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "daily-20260916"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({
                "status": "completed", "publish_status": "published",
            }))
            calls = []

            def step(command):
                calls.append(Path(command[1]).name)
                class Result:
                    returncode = 0
                    stdout = '{"status":"already_sent"}'
                    stderr = ""
                return Result()

            with patch("spectra_agent.daily_delivery.resolve_config", return_value=(root / "config.json", {})), \
                    patch("spectra_agent.daily_delivery.runs_dir", return_value=root), \
                    patch("spectra_agent.daily_delivery.run_step", side_effect=step), \
                    patch("sys.argv", ["daily_delivery.py", "--run-id", "daily-20260916"]):
                self.assertEqual(main(), 0)
            self.assertEqual(calls, ["dingtalk_push.py"])


if __name__ == "__main__":
    unittest.main()
