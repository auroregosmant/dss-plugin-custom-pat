import ast
from pathlib import PurePosixPath

from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class TestDetectionVisitor(ast.NodeVisitor):
    """Detect unittest- or pytest-style tests using the original rules."""

    def __init__(self):
        self.has_unittest_import = False
        self.has_pytest_import = False
        self.has_testcase_class = False
        self.has_test_function = False
        self.has_pytest_mark = False

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name == "unittest":
                self.has_unittest_import = True
            if alias.name == "pytest":
                self.has_pytest_import = True
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module == "unittest":
            self.has_unittest_import = True
        if node.module == "pytest":
            self.has_pytest_import = True
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        for base in node.bases:
            if (
                isinstance(base, ast.Attribute)
                and base.attr == "TestCase"
            ) or (
                isinstance(base, ast.Name)
                and base.id == "TestCase"
            ):
                self.has_testcase_class = True
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        if node.name.startswith("test_"):
            self.has_test_function = True

        for decorator in node.decorator_list:
            if self._is_pytest_mark(decorator):
                self.has_pytest_mark = True

        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        if node.name.startswith("test_"):
            self.has_test_function = True

        for decorator in node.decorator_list:
            if self._is_pytest_mark(decorator):
                self.has_pytest_mark = True

        self.generic_visit(node)

    def _is_pytest_mark(self, decorator):
        """Detect pytest attribute decorators, with or without arguments."""
        if isinstance(decorator, ast.Attribute):
            return self._attribute_starts_with_pytest(decorator)

        if isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Attribute):
                return self._attribute_starts_with_pytest(decorator.func)

        return False

    def _attribute_starts_with_pytest(self, node):
        current = node
        while isinstance(current, ast.Attribute):
            current = current.value
        return isinstance(current, ast.Name) and current.id == "pytest"

    def has_tests(self):
        return any([
            self.has_test_function,
            self.has_testcase_class,
            self.has_pytest_mark,
            self.has_unittest_import,
            self.has_pytest_import,
        ])


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Scan project Python libraries and recipes for test indicators.

    Preserve the original success/failure rules and severity.
    Stop and return error() if reading or parsing fails.
    """

    MAX_REPORTED_TESTS = 50

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
            # Severity remains exactly as configured.
            severity = int(self.config["severity"])

            results = {
                "analyzed_library_file_count": 0,
                "analyzed_recipe_count": 0,
                "analyzed_file_count": 0,
                "detected_test_count": 0,
                "reported_test_count": 0,
                "tests_truncated": False,
                "detected_tests": [],
            }

            self._scan_library(results)
            self._scan_python_recipes(results)

            results["analyzed_file_count"] = (
                results["analyzed_library_file_count"]
                + results["analyzed_recipe_count"]
            )
            results["reported_test_count"] = len(
                results["detected_tests"]
            )
            results["tests_truncated"] = (
                results["detected_test_count"]
                > results["reported_test_count"]
            )

            if results["detected_test_count"]:
                return ProjectStandardsCheckRunResult.success(
                    message="Python tests detected in the project.",
                    details=results,
                )

            return ProjectStandardsCheckRunResult.failure(
                severity=severity,
                message="No valid Python tests found in the project.",
                details=results,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=f"Error while checking Python tests: {exc}"
            )

    def _scan_library(self, results):
        """Scan Python library files outside excluded folders."""
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

            # Keep __init__.py included, as in the original check.
            try:
                content = item.read() or ""
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
        """Scan all Python recipes using the original selection rule."""
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
        """Count every matching source and retain a limited sample."""
        tree = ast.parse(code, filename=source_name)
        visitor = TestDetectionVisitor()
        visitor.visit(tree)

        if not visitor.has_tests():
            return

        # As before, this counts matching sources, not test functions.
        results["detected_test_count"] += 1

        if len(results["detected_tests"]) >= self.MAX_REPORTED_TESTS:
            return

        results["detected_tests"].append({
            "source": source_name,
            "has_unittest_import": visitor.has_unittest_import,
            "has_pytest_import": visitor.has_pytest_import,
            "has_testcase_class": visitor.has_testcase_class,
            "has_test_function": visitor.has_test_function,
            "has_pytest_mark": visitor.has_pytest_mark,
        })