import tempfile
import unittest
from pathlib import Path

from mini_cmux.cli import _read_cursor, _write_cursor, build_parser


class CliTests(unittest.TestCase):
    def test_agent_create_supports_original_flag_form(self):
        args = build_parser().parse_args(
            [
                "agent",
                "create",
                "--project",
                "demo",
                "--name",
                "planner",
                "--role",
                "planner",
                "--command",
                "pi",
            ]
        )
        self.assertEqual(args.root_command, "agent")
        self.assertEqual(args.project_option, "demo")
        self.assertEqual(args.name_option, "planner")
        self.assertEqual(args.command, "pi")

    def test_agent_create_supports_compact_form(self):
        args = build_parser().parse_args(
            ["agent", "create", "demo", "planner", "--command", "pi"]
        )
        self.assertEqual(args.project_arg, "demo")
        self.assertEqual(args.name_arg, "planner")
        self.assertIsNone(args.role)

    def test_project_cwd_defaults_to_current_directory(self):
        args = build_parser().parse_args(["project", "create", "demo"])
        self.assertEqual(args.cwd, ".")

    def test_control_plane_commands_parse(self):
        events = build_parser().parse_args(
            [
                "events",
                "--after-seq",
                "4",
                "--cursor-file",
                "/tmp/cursor",
                "--type",
                "agent_completed",
                "--follow",
            ]
        )
        self.assertEqual(events.after_seq, 4)
        self.assertEqual(events.event_types, ["agent_completed"])
        hook = build_parser().parse_args(
            ["hook", "waiting", "--agent", "worker", "--project", "demo"]
        )
        self.assertEqual(hook.status, "waiting")
        attention = build_parser().parse_args(
            ["attention", "ack", "--all", "--project", "demo"]
        )
        self.assertTrue(attention.all)

    def test_event_cursor_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "events.seq"
            self.assertIsNone(_read_cursor(str(path)))
            _write_cursor(
                str(path),
                [{"seq": 4}, {"seq": 9}, {"seq": 7}],
            )
            self.assertEqual(_read_cursor(str(path)), 9)


if __name__ == "__main__":
    unittest.main()
