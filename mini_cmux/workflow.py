"""A deliberately small planner/implementer/verifier workflow."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .controller import Controller
from .errors import MiniCmuxError


class WorkflowRunner:
    def __init__(self, controller: Controller) -> None:
        self.controller = controller

    def run(
        self,
        project: str,
        planner: str = "planner",
        implementer: str = "implementer",
        verifier: str = "verifier",
        goal_file: str = "GOAL.md",
        plan_file: str = "PLAN.md",
        max_repairs: int = 1,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        if max_repairs < 0:
            raise MiniCmuxError("--max-repairs cannot be negative")

        planner_prompt = (
            "Read {goal}, produce a concrete implementation plan in {plan}, "
            "and finish by printing exactly AGENT_STATUS=PLAN_READY."
        ).format(goal=goal_file, plan=plan_file)
        self.controller.dispatch(planner, planner_prompt, project)
        planner_state = self.controller.wait(
            planner, "plan_ready", project, timeout=timeout
        )

        implementation_prompt = (
            "Read {goal} and {plan}. Implement the plan, run the relevant tests, "
            "and finish by printing exactly AGENT_STATUS=DONE. If blocked on a "
            "human decision, print AGENT_STATUS=WAITING_FOR_INPUT."
        ).format(goal=goal_file, plan=plan_file)
        self.controller.dispatch(implementer, implementation_prompt, project)
        implementer_state = self.controller.wait(
            implementer, "completed", project, timeout=timeout
        )

        review_prompt = (
            "Read {goal} and {plan}. Review the current diff and run the tests. "
            "Explain actionable problems, then finish with exactly "
            "REVIEW_STATUS=GREEN or REVIEW_STATUS=RED."
        ).format(goal=goal_file, plan=plan_file)

        repairs = 0
        while True:
            self.controller.dispatch(verifier, review_prompt, project)
            verifier_state = self.controller.wait_for_any(
                verifier,
                {"review_green", "review_red"},
                project,
                timeout=timeout,
            )
            if verifier_state["status"] == "review_green":
                return {
                    "status": "completed",
                    "project": project,
                    "planner": planner_state,
                    "implementer": implementer_state,
                    "verifier": verifier_state,
                    "repair_rounds": repairs,
                }
            if repairs >= max_repairs:
                raise MiniCmuxError(
                    "Verifier reported RED after {} repair round(s)".format(repairs)
                )
            feedback = self.controller.read_agent(
                verifier, lines=200, project_name=project
            )
            repair_prompt = (
                "The verifier reported REVIEW_STATUS=RED. Address this feedback, "
                "rerun the relevant tests, and finish with AGENT_STATUS=DONE:\n\n"
                + feedback
            )
            self.controller.dispatch(implementer, repair_prompt, project)
            implementer_state = self.controller.wait(
                implementer, "completed", project, timeout=timeout
            )
            repairs += 1

