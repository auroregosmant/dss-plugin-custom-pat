import re
from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Available attributes from the superclass:
    - self.config: dict containing the check configuration
    - self.plugin_config: dict containing the plugin settings
    - self.project: current DSSProject

    This check verifies:
    - Failure (with severity): no custom Python library exists in lib/
    - Success: custom Python library exists and is used in at least one Python recipe
    - Error: custom Python library exists but is not used in any Python recipe
    """

    IMPORT_PATTERN = re.compile(
        r"^\s*(?:import|from)\s+([a-zA-Z_][\w\.]*)",
        re.MULTILINE,
    )

    def run(self):
        try:
            severity = int(self.config["severity"])

            lib_modules = self._get_lib_modules()
            if not lib_modules:
                return ProjectStandardsCheckRunResult.failure(
                    severity=severity,
                    message="No custom Python files found in lib/.",
                    details={
                        "lib_modules": [],
                        "python_recipe_count": 0,
                        "matched_import_count": 0,
                        "matched_imports": [],
                    },
                )

            matched_imports, scanned_recipes = self._scan_python_recipes(lib_modules)

            details = {
                "lib_modules": sorted(lib_modules),
                "python_recipe_count": scanned_recipes,
                "matched_import_count": len(matched_imports),
                "matched_imports": matched_imports,
            }

            if matched_imports:
                recipes = sorted({match["recipe"] for match in matched_imports})
                return ProjectStandardsCheckRunResult.success(
                    message="Custom library is used in Python recipes: {}".format(
                        ", ".join(recipes)
                    ),
                    details=details,
                )

            return ProjectStandardsCheckRunResult.failure(
                severity=severity,
                message=(
                    "Custom Python files exist in lib/, but none are imported "
                    "by any Python recipe."
                ),
                details=details,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=f"Error while checking custom library usage: {exc}"
            )

    def _get_lib_modules(self):
        """
        Discover top-level Python modules from the project library.
        """
        library = self.project.get_library()
        lib_items = library.list()

        lib_modules = set()
        self._walk_lib_items(lib_items, lib_modules)
        return lib_modules

    def _walk_lib_items(self, items, lib_modules):
        for item in items:
            path = getattr(item, "path", "")

            if hasattr(item, "list"):
                try:
                    self._walk_lib_items(item.list(), lib_modules)
                except Exception:
                    continue
                continue

            if not path.endswith(".py"):
                continue

            filename = path.split("/")[-1]
            if filename.startswith("__"):
                continue

            clean_path = path.lstrip("/")
            if clean_path.startswith("python/"):
                clean_path = clean_path[len("python/"):]

            module_path = clean_path[:-3].replace("/", ".")
            top_level = module_path.split(".")[0].strip()

            if top_level:
                lib_modules.add(top_level)

    def _scan_python_recipes(self, lib_modules):
        """
        Scan Python recipes and collect imports matching project lib modules.
        """
        matched = set()
        scanned_recipes = 0

        for recipe_info in self.project.list_recipes():
            if recipe_info.get("type") != "python":
                continue

            recipe_name = recipe_info.get("name")
            scanned_recipes += 1

            try:
                recipe = self.project.get_recipe(recipe_name)
                code = recipe.get_settings().get_payload() or ""

                for imported_module in self.IMPORT_PATTERN.findall(code):
                    top_level = imported_module.split(".")[0]
                    if top_level in lib_modules:
                        matched.add((recipe_name, imported_module))

            except Exception:
                continue

        matched_imports = [
            {"recipe": recipe, "import": imported_module}
            for recipe, imported_module in sorted(matched)
        ]

        return matched_imports, scanned_recipes