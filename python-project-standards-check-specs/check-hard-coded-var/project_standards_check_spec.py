from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)

import HardcodedVariableFinder
from HardcodedVariableFinder import find_hardcoded_variables

class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Check if Python recipes contain hard-coded variables.
    """

    def run(self):
        severity = int(self.config["severity"])
        project = self.project

        recipes_with_hardcoded_variables = {}
        unreadable_recipes = []

        python_recipes = [
            r for r in project.list_recipes()
            if r.get("type") == "python"
        ]

        if not python_recipes:
            return ProjectStandardsCheckRunResult.not_applicable(
                message="Check is skipped. Project doesn't have a Python recipe."
            )

        for recipe_info in python_recipes:
            recipe_name = recipe_info.get("name")

            try:
                recipe = project.get_recipe(recipe_name)
                settings = recipe.get_settings()

                python_code = None

                if hasattr(settings, "get_code"):
                    python_code = settings.get_code()

                if python_code is None and hasattr(settings, "get_payload"):
                    python_code = settings.get_payload()

                if not isinstance(python_code, str):
                    unreadable_recipes.append(recipe_name)
                    continue

                hardcoded = find_hardcoded_variables(python_code)

                # Only keep recipes that actually contain hard-coded variables
                if hardcoded:
                    recipes_with_hardcoded_variables[recipe_name] = hardcoded

            except Exception:
                unreadable_recipes.append(recipe_name)

        details = {
            "unreadable_recipe_count": len(unreadable_recipes),
            "unreadable_recipes": unreadable_recipes,
            "hardcoded_recipe_count": len(recipes_with_hardcoded_variables),
            "recipes_with_hardcoded_variables": recipes_with_hardcoded_variables,
        }

        if recipes_with_hardcoded_variables:
            return ProjectStandardsCheckRunResult.failure(
                severity=severity,
                message="The project has hard-coded variables.",
                details=details,
            )

        return ProjectStandardsCheckRunResult.success(
            message="The project doesn't have hard-coded variables.",
            details=details,
        )