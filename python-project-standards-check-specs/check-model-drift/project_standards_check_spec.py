import dataiku
from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class ProjectStandardsCheck(ProjectStandardsCheckSpec):
    """
    Project Standards check:
    If the project contains at least one saved model, it must also have:
      1. at least one Model Evaluation Store
      2. at least one scenario that builds a Model Evaluation Store
    """

    def run(self):
        project = self.project
        severity = int(self.config.get("severity", 1))

        saved_models = project.list_saved_models()
        if not saved_models:
            return ProjectStandardsCheckRunResult.not_applicable(
                message="Check is skipped. Project doesn't have a saved model in the flow."
            )

        evaluation_stores = project.list_model_evaluation_stores()
        if not evaluation_stores:
            return ProjectStandardsCheckRunResult.failure(
                severity=severity,
                message="Project has saved model(s) but no model evaluation store.",
                details={"scenario_count": 0},
            )

        monitoring_scenarios = self._find_mes_build_scenarios(project)

        if monitoring_scenarios:
            return ProjectStandardsCheckRunResult.success(
                message=f"Drift monitoring found in scenarios: {sorted(monitoring_scenarios)}",
                details={"scenario_count": len(monitoring_scenarios)},
            )

        return ProjectStandardsCheckRunResult.failure(
            severity=severity,
            message="Project has a model evaluation store but no scenario builds it.",
            details={"scenario_count": 0},
        )

    def _find_mes_build_scenarios(self, project):
        """
        Return a set of scenario IDs that contain at least one step
        building a MODEL_EVALUATION_STORE.
        """
        matching_scenarios = set()

        for scenario_info in project.list_scenarios(as_type="objects"):
            scenario_id = scenario_info.id

            try:
                scenario = project.get_scenario(scenario_id)
                raw_settings = scenario.get_settings().get_raw()
                steps = raw_settings.get("params", {}).get("steps", [])
            except Exception:
                # Skip scenarios whose settings cannot be read
                continue

            if self._scenario_builds_mes(steps):
                matching_scenarios.add(scenario_id)

        return matching_scenarios

    def _scenario_builds_mes(self, steps):
        """
        Return True if any scenario step builds a MODEL_EVALUATION_STORE.
        """
        for step in steps:
            builds = step.get("params", {}).get("builds", [])

            for build in builds:
                if build.get("type") == "MODEL_EVALUATION_STORE":
                    return True

        return False