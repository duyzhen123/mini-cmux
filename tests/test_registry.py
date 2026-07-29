import json
import tempfile
import unittest
from pathlib import Path

from mini_cmux.registry import Registry


class RegistryTests(unittest.TestCase):
    def test_mutation_is_persisted_and_event_log_is_appended(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Registry(Path(directory))

            def change(state):
                state["projects"]["p1"] = {"id": "p1", "name": "demo"}
                registry.append_event_unlocked(
                    state, {"id": "e1", "type": "agent_started"}
                )

            registry.mutate(change)
            state = registry.read()
            self.assertEqual(state["projects"]["p1"]["name"], "demo")
            self.assertEqual(state["events"][0]["id"], "e1")
            self.assertEqual(state["events"][0]["seq"], 1)
            self.assertEqual(state["next_event_seq"], 2)
            self.assertEqual(state["hook_receipts"], {})
            lines = registry.events_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0])["type"], "agent_started")


if __name__ == "__main__":
    unittest.main()
