import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TeamDemoTests(unittest.TestCase):
    def test_example_shell_scripts_parse(self):
        root = Path(__file__).resolve().parent.parent
        for script in [
            root / "examples" / "team-demo" / "run-demo.sh",
            root / "examples" / "hook-adapter" / "run-job-with-hooks.sh",
        ]:
            subprocess.run(
                ["bash", "-n", str(script)],
                text=True,
                capture_output=True,
                check=True,
            )

    def test_planner_implementer_verifier_handoff(self):
        script = (
            Path(__file__).resolve().parent.parent
            / "examples"
            / "team-demo"
            / "demo_agent.py"
        )
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            planner = subprocess.run(
                [sys.executable, str(script), "planner"],
                cwd=str(cwd),
                input="plan the work\n",
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("AGENT_STATUS=PLAN_READY", planner.stdout)
            self.assertTrue((cwd / "PLAN.md").exists())

            implementer = subprocess.run(
                [sys.executable, str(script), "implementer"],
                cwd=str(cwd),
                input="implement the plan\n",
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("AGENT_STATUS=DONE", implementer.stdout)

            verifier = subprocess.run(
                [sys.executable, str(script), "verifier"],
                cwd=str(cwd),
                input="verify the result\n",
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("REVIEW_STATUS=GREEN", verifier.stdout)
            self.assertEqual(
                (cwd / "demo_output.txt").read_text(encoding="utf-8"),
                "mini-cmux team handoff complete\n",
            )


if __name__ == "__main__":
    unittest.main()
