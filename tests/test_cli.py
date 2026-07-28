import unittest

from mini_cmux.cli import build_parser


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


if __name__ == "__main__":
    unittest.main()

