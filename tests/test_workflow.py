import unittest

from mini_cmux.workflow import WorkflowRunner


class ScriptedController:
    def __init__(self, review_statuses):
        self.review_statuses = list(review_statuses)
        self.dispatched = []

    def dispatch(self, name, text, project):
        self.dispatched.append((name, text, project))
        return {"name": name, "status": "working"}

    def wait(self, name, status, project, timeout=None):
        return {"name": name, "status": status}

    def wait_for_any(self, name, statuses, project, timeout=None):
        return {"name": name, "status": self.review_statuses.pop(0)}

    def read_agent(self, name, lines, project_name=None):
        return "Fix the failing edge-case test.\nREVIEW_STATUS=RED\n"


class WorkflowTests(unittest.TestCase):
    def test_green_path(self):
        controller = ScriptedController(["review_green"])
        result = WorkflowRunner(controller).run("demo")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_rounds"], 0)
        self.assertEqual(
            [item[0] for item in controller.dispatched],
            ["planner", "implementer", "verifier"],
        )

    def test_one_red_routes_feedback_then_reviews_again(self):
        controller = ScriptedController(["review_red", "review_green"])
        result = WorkflowRunner(controller).run("demo", max_repairs=1)
        self.assertEqual(result["repair_rounds"], 1)
        names = [item[0] for item in controller.dispatched]
        self.assertEqual(
            names, ["planner", "implementer", "verifier", "implementer", "verifier"]
        )
        self.assertIn("failing edge-case", controller.dispatched[3][1])


if __name__ == "__main__":
    unittest.main()

