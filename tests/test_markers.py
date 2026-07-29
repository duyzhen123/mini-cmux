import unittest

from mini_cmux.markers import all_markers, interpret_marker, last_marker


class MarkerTests(unittest.TestCase):
    def test_finds_last_structured_marker(self):
        marker = last_marker(
            "AGENT_STATUS=WORKING\nnoise\nAGENT_STATUS=WAITING_FOR_INPUT\n"
        )
        self.assertEqual(marker["raw"], "AGENT_STATUS=WAITING_FOR_INPUT")
        self.assertEqual(
            interpret_marker(marker),
            {
                "status": "waiting_for_input",
                "event": "agent_waiting_for_input",
            },
        )

    def test_review_markers(self):
        marker = last_marker("tests complete\nREVIEW_STATUS=GREEN\n")
        self.assertEqual(
            interpret_marker(marker),
            {"status": "review_green", "event": "review_green"},
        )

    def test_returns_all_markers_in_order(self):
        self.assertEqual(
            [item["raw"] for item in all_markers(
                "AGENT_STATUS=WORKING\nAGENT_STATUS=DONE"
            )],
            ["AGENT_STATUS=WORKING", "AGENT_STATUS=DONE"],
        )

    def test_ignores_lowercase_and_embedded_values(self):
        self.assertIsNone(last_marker("agent_status=done"))
        self.assertIsNone(last_marker("XAGENT_STATUS=DONE"))
        self.assertIsNone(
            last_marker(
                "Finish by printing AGENT_STATUS=DONE. If blocked, print "
                "AGENT_STATUS=WAITING_FOR_INPUT."
            )
        )
        self.assertIsNone(last_marker("Result: REVIEW_STATUS=GREEN"))

    def test_accepts_whitespace_and_terminal_ansi_around_exact_line(self):
        marker = last_marker("\x1b[32m  AGENT_STATUS=DONE  \x1b[0m\r\n")
        self.assertEqual(marker["raw"], "AGENT_STATUS=DONE")


if __name__ == "__main__":
    unittest.main()
