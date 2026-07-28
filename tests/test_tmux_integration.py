import os
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from mini_cmux.controller import Controller
from mini_cmux.notifications import Notifier
from mini_cmux.registry import Registry
from mini_cmux.tmux import Tmux


@unittest.skipUnless(shutil.which("tmux"), "tmux is required")
class TmuxIntegrationTests(unittest.TestCase):
    def test_real_tmux_lifecycle_capture_marker_and_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            socket_path = os.path.join(
                directory, "tmux-{}.sock".format(uuid.uuid4().hex)
            )
            tmux = Tmux(socket_path=socket_path)
            controller = Controller(
                registry=Registry(Path(directory) / "state"),
                tmux=tmux,
                notifier=Notifier(enabled=False),
            )
            project = controller.create_project("integration", directory)
            try:
                agent = controller.create_agent(
                    "integration",
                    "worker",
                    "implementer",
                    "printf 'hello\\nAGENT_STATUS=DONE\\n'",
                )
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    controller.reconcile()
                    current = controller.list_agents("integration")[0]
                    if current["last_exit_status"] is not None:
                        break
                    time.sleep(0.05)
                output = controller.read_agent("worker", 20, "integration")
                self.assertIn("AGENT_STATUS=DONE", output)
                current = controller.list_agents("integration")[0]
                self.assertEqual(current["id"], agent["id"])
                self.assertEqual(current["status"], "completed")
                self.assertEqual(current["last_exit_status"], 0)
                event_types = [
                    event["type"] for event in controller.events("integration")
                ]
                self.assertIn("agent_started", event_types)
                self.assertIn("agent_completed", event_types)
                self.assertIn("process_exited", event_types)
            finally:
                controller.cleanup(project["name"])


if __name__ == "__main__":
    unittest.main()

