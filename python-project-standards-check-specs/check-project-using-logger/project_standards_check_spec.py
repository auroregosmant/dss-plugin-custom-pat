import ast
from pathlib import PurePosixPath

from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Scan project Python libraries and recipes.

    Original rules:
    - failure: at least one print() call is found.
    - success: no print() calls and logging is detected.
    - failure: neither print() nor logging is detected.
    - error: reading or parsing fails.

    Exclude dependency folders and limit reported print issues.
    """

    MAX_REPORTED_ISSUES = 50

    LOGGING_METHODS = {
        "debug",
        "info",
        "warning",
        "error",
        "exception",
        "critical",
        "log",
    }

    EXCLUDED_DIRS = frozenset({
        ".ven", "ven", ".venv", "venv", ".env", "env",
        ".git", ".hg", ".svn",
        "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", ".tox", ".nox",
        ".idea", ".vscode",
        "node_modules", "site-packages",
        "build", "dist", "vendor", "third_party",
    })

    def run(self):
        try:
            # Preserve the original configured severity.
            severity = int(self.config["severity"])

            results = {
                "analyzed_library_file_count": 0,
                "analyzed_recipe_count": 0,
                "print_issue_count": 0,
                "logging_usage_count": 0,
                "print_issues": [],
            }

            self._scan_library(results)
            self._scan_python_recipes(results)

            results["analyzed_file_count"] = (
                results["analyzed_library_file_count"]
                + results["analyzed_recipe_count"]
            )
            results["reported_print_issue_count"] = len(
                results["print_issues"]
            )
            results["issues_truncated"] = (
                results["print_issue_count"]
                > results["reported_print_issue_count"]
            )

            if results["print_issue_count"]:
                return ProjectStandardsCheckRunResult.failure(
                    severity=severity,
                    message=(
                        f"{results['print_issue_count']} print() "
                        "statement(s) found. "
                        "Use logging instead of print()."
                    ),
                    details=results,
                )

            if results["logging_usage_count"]:
                return ProjectStandardsCheckRunResult.success(
                    message=(
                        "No print() statements found, "
                        "and logging is used at least once."
                    ),
                    details=results,
                )

            return ProjectStandardsCheckRunResult.failure(
                severity=severity,
                message=(
                    "No print() statements found, "
                    "but no logging usage was detected."
                ),
                details=results,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=f"Error while checking print/logging usage: {exc}"
            )

    def _scan_library(self, results):
        """Scan Python files outside excluded directories."""
        try:
            library = self.project.get_library()
            items = library.list()
        except Exception as exc:
            raise RuntimeError(
                "Error accessing project library: "
                f"{type(exc).__name__}"
            ) from None

        self._walk_library_items(items, results)

    def _walk_library_items(self, items, results):
        for item in items:
            path = str(getattr(item, "path", ""))
            normalized_path = path.replace("\\", "/")
            parts = PurePosixPath(normalized_path).parts

            # Exclude BEFORE listing folders or reading files.
            if any(part in self.EXCLUDED_DIRS for part in parts):
                continue

            if callable(getattr(item, "list", None)):
                try:
                    children = item.list()
                except Exception as exc:
                    raise RuntimeError(
                        f"Error listing folder '{path}': "
                        f"{type(exc).__name__}"
                    ) from None

                self._walk_library_items(children, results)
                continue

            if not normalized_path.endswith(".py"):
                continue

            # __init__.py remains included, as in the original.
            try:
                content = item.read()
                self._analyze_python_code(
                    code=content,
                    source_name=path,
                    results=results,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Error reading/parsing library file '{path}': "
                    f"{type(exc).__name__}"
                ) from None

            results["analyzed_library_file_count"] += 1

    def _scan_python_recipes(self, results):
        """Scan all Python recipes."""
        try:
            recipes = self.project.list_recipes()
        except Exception as exc:
            raise RuntimeError(
                "Error listing project recipes: "
                f"{type(exc).__name__}"
            ) from None

        for recipe_info in recipes:
            if recipe_info.get("type") != "python":
                continue

            recipe_name = recipe_info.get("name")
            source_name = f"recipe:{recipe_name}"

            try:
                recipe = self.project.get_recipe(recipe_name)
                code = recipe.get_settings().get_payload() or ""

                self._analyze_python_code(
                    code=code,
                    source_name=source_name,
                    results=results,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Error reading/parsing Python recipe '{recipe_name}': "
                    f"{type(exc).__name__}"
                ) from None

            results["analyzed_recipe_count"] += 1

    def _analyze_python_code(self, code, source_name, results):
        """Count all calls while retaining bounded print examples."""
        tree = ast.parse(code, filename=source_name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if self._is_print_call(node):
                results["print_issue_count"] += 1

                if (
                    len(results["print_issues"])
                    < self.MAX_REPORTED_ISSUES
                ):
                    results["print_issues"].append({
                        "source": source_name,
                        "line": getattr(node, "lineno", "?"),
                        "issue": "print() statement found",
                    })

            if self._is_logging_call(node):
                results["logging_usage_count"] += 1

    def _is_print_call(self, node):
        """Preserve the original print() detection."""
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "print"
        )

    def _is_logging_call(self, node):
        """Preserve the original logging detection."""
        if not isinstance(node.func, ast.Attribute):
            return False

        return node.func.attr in self.LOGGING_METHODS