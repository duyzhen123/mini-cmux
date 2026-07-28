import tempfile
import unittest
from pathlib import Path

from mini_cmux.controller import Controller
from mini_cmux.errors import MiniCmuxError
from mini_cmux.notifications import Notifier
from mini_cmux.registry import Registry


class FakeTmux:
    def __init__(self):
        self.sessions = {}
        self.sent = []
        self.keys = []
        self.next_pane = 1
        self.base_command = ["tmux"]

    def session_exists(self, session):
        return session in self.sessions

    def create_session(self, session, cwd, project_id):
        self.sessions[session] = {
            "project_id": project_id,
            "panes": {},
            "cwd": cwd,
        }
        return "%0"

    def kill_session(self, session, missing_ok=False):
        self.sessions.pop(session, None)

    def create_agent_pane(
        self, session, project_id, agent_id, name, role, cwd, command
    ):
        pane_id = "%{}".format(self.next_pane)
        self.next_pane += 1
        pane = {
            "pane_id": pane_id,
            "session": session,
            "window": "review" if role == "verifier" else "main",
            "window_index": 1,
            "pane_index": self.next_pane - 2,
            "dead": False,
            "dead_status": None,
            "pid": 1000 + self.next_pane,
            "title": "mini-cmux:{}:{}".format(name, agent_id[:8]),
            "agent_id": agent_id,
            "target": "{}:1.{}".format(session, self.next_pane - 2),
            "capture": "",
        }
        self.sessions[session]["panes"][pane_id] = pane
        return pane_id

    def list_panes(self, session):
        return [
            dict(pane, capture=None)
            for pane in self.sessions[session]["panes"].values()
        ]

    def capture(self, pane_id, lines):
        for session in self.sessions.values():
            if pane_id in session["panes"]:
                return session["panes"][pane_id]["capture"]
        raise MiniCmuxError("missing pane")

    def send_text(self, pane, text):
        self.sent.append((pane, text))

    def send_key(self, pane, key):
        self.keys.append((pane, key))

    def select_pane(self, pane):
        self.selected = pane

    def kill_pane(self, pane, missing_ok=False):
        for session in self.sessions.values():
            session["panes"].pop(pane, None)

    def attach(self, session):
        return 0

    def switch_client(self, session):
        self.switched = session


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.tmux = FakeTmux()
        self.controller = Controller(
            registry=Registry(Path(self.temporary.name)),
            tmux=self.tmux,
            notifier=Notifier(enabled=False),
        )
        self.cwd = self.temporary.name

    def tearDown(self):
        self.temporary.cleanup()

    def test_project_agent_send_read_and_marker_event(self):
        project = self.controller.create_project("demo", self.cwd)
        agent = self.controller.create_agent(
            "demo", "planner", "planner", "pi", self.cwd
        )
        self.controller.send_text("planner", "Write PLAN.md", "demo")
        self.controller.send_key("planner", "enter", "demo")
        self.assertEqual(self.tmux.sent[-1], (agent["tmux_pane"], "Write PLAN.md"))
        self.assertEqual(self.tmux.keys[-1], (agent["tmux_pane"], "enter"))

        pane = self.tmux.sessions[project["tmux_session"]]["panes"][
            agent["tmux_pane"]
        ]
        pane["capture"] = "finished\nAGENT_STATUS=PLAN_READY\n"
        events = self.controller.reconcile()
        self.assertEqual(events[0]["type"], "agent_completed")
        updated = self.controller.list_agents("demo")[0]
        self.assertEqual(updated["status"], "plan_ready")
        self.assertTrue(updated["attention_required"])
        self.assertIn("PLAN_READY", self.controller.read_agent("planner", 20))

        self.assertEqual(self.controller.reconcile(), [])

    def test_process_exit_is_detected(self):
        project = self.controller.create_project("demo", self.cwd)
        agent = self.controller.create_agent(
            "demo", "worker", "implementer", "true", self.cwd
        )
        pane = self.tmux.sessions[project["tmux_session"]]["panes"][
            agent["tmux_pane"]
        ]
        pane["dead"] = True
        pane["dead_status"] = 7
        events = self.controller.reconcile()
        self.assertEqual(events[0]["type"], "process_exited")
        self.assertEqual(self.controller.list_agents("demo")[0]["status"], "failed")

    def test_repair_updates_changed_pane_id_from_stable_agent_metadata(self):
        project = self.controller.create_project("demo", self.cwd)
        agent = self.controller.create_agent(
            "demo", "worker", "implementer", "sleep 10", self.cwd
        )
        panes = self.tmux.sessions[project["tmux_session"]]["panes"]
        pane = panes.pop(agent["tmux_pane"])
        pane["pane_id"] = "%99"
        pane["target"] = "{}:1.9".format(project["tmux_session"])
        panes["%99"] = pane

        self.controller.reconcile()
        repaired = self.controller.list_agents("demo")[0]
        self.assertEqual(repaired["tmux_pane"], "%99")
        self.assertEqual(repaired["tmux_target"], pane["target"])

    def test_agent_names_must_be_qualified_when_ambiguous(self):
        self.controller.create_project("one", self.cwd)
        self.controller.create_project("two", self.cwd)
        self.controller.create_agent("one", "worker", "implementer", "pi")
        self.controller.create_agent("two", "worker", "implementer", "pi")
        with self.assertRaisesRegex(MiniCmuxError, "ambiguous"):
            self.controller.send_text("worker", "hello")
        self.controller.send_text("worker", "hello", "two")

    def test_lost_session_and_safe_cleanup(self):
        project = self.controller.create_project("demo", self.cwd)
        self.controller.create_agent("demo", "worker", "implementer", "pi")
        self.tmux.sessions.pop(project["tmux_session"])
        events = self.controller.reconcile()
        self.assertEqual(events[0]["type"], "session_lost")
        state = self.controller.status("demo")
        self.assertEqual(next(iter(state["projects"].values()))["status"], "lost")
        self.assertEqual(next(iter(state["agents"].values()))["status"], "lost")
        self.assertEqual(self.controller.reconcile(), [])
        closed = self.controller.cleanup("demo")
        self.assertEqual(closed["status"], "closed")


if __name__ == "__main__":
    unittest.main()

