import unittest
from pathlib import Path

from spectra_agent.install_werss_service import LABEL, launch_agent_payload


class WeRSSServiceTest(unittest.TestCase):
    def test_launch_agent_starts_on_login_and_restarts_after_failure(self):
        payload = launch_agent_payload()
        self.assertEqual(payload["Label"], LABEL)
        self.assertTrue(payload["RunAtLoad"])
        self.assertTrue(payload["KeepAlive"])
        self.assertEqual(payload["ProgramArguments"][1:4], ["-m", "uvicorn", "web:app"])
        self.assertEqual(Path(payload["WorkingDirectory"]).name, "we-mp-rss")
        self.assertIn("Application Support/SPECTRA/data/logs", payload["StandardErrorPath"])


if __name__ == "__main__":
    unittest.main()
