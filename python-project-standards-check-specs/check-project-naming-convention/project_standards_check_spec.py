import ast
import re
from pathlib import PurePosixPath

from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


# Original naming patterns: unchanged.
SNAKE_CASE = re.compile(r"^[a-z]+(_[a-z0-9]+)*$")
CAMEL_CASE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
UPPER_CASE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def check_snake_case(name):
    return bool(SNAKE_CASE.match(name))


def check_camel_case(name):
    return bool(CAMEL_CASE.match(name))


def check_upper_case(name):
    return bool(UPPER_CASE.match(name))


class BoundedIssues(list):
    """Count every issue while retaining a limited sample."""

    def __init__(self, limit=50):
        super().__init__()
        self.limit = limit
        self.total_count = 0

    def append(self, issue):
        self.total_count += 1
        if len(self) < self.limit:
            super().append(issue)


class StyleVisitor(ast.NodeVisitor):
    """Check naming conventions using the original detection rules."""

    def __init__(self, max_reported_issues=50):
        self.issues = BoundedIssues(limit=max_reported_issues)

    def visit_FunctionDef(self, node):
        if not check_snake_case(node.name):
            self.issues.append((
                node.lineno,
                f"Function '{node.name}' should be snake_case",
            ))

        self._check_arguments(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        if not check_snake_case(node.name):
            self.issues.append((
                node.lineno,
                f"Async function '{node.name}' should be snake_case",
            ))

        self._check_arguments(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        if not check_camel_case(node.name):
            self.issues.append((
                node.lineno,
                f"Class '{node.name}' should be CamelCase",
            ))
        self.generic_visit(node)

    def visit_Assign(self, node):
        for target in node.targets:
            self._check_assignment_target(target, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._check_assignment_target(node.target, node.lineno)
        self.generic_visit(node)

    def visit_For(self, node):
        self._check_assignment_target(
            node.target, node.lineno, context="Loop variable"
        )
        self.generic_visit(node)

    def visit_AsyncFor(self, node):
        self._check_assignment_target(
            node.target, node.lineno, context="Loop variable"
        )
        self.generic_visit(node)

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars is not None:
                self._check_assignment_target(
                    item.optional_vars,
                    node.lineno,
                    context="With variable",
                )
        self.generic_visit(node)

    def visit_AsyncWith(self, node):
        for item in node.items:
            if item.optional_vars is not None:
                self._check_assignment_target(
                    item.optional_vars,
                    node.lineno,
                    context="With variable",
                )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if isinstance(node.name, str):
            if not check_snake_case(node.name):
                self.issues.append((
                    node.lineno,
                    f"Exception variable '{node.name}' should be snake_case",
                ))
        self.generic_visit(node)

    def _check_arguments(self, node):
        all_args = []
        all_args.extend(getattr(node.args, "posonlyargs", []))
        all_args.extend(node.args.args)
        all_args.extend(node.args.kwonlyargs)

        if node.args.vararg is not None:
            all_args.append(node.args.vararg)

        if node.args.kwarg is not None:
            all_args.append(node.args.kwarg)

        for arg in all_args:
            arg_name = arg.arg

            if arg_name in {"self", "cls"}:
                continue

            if not check_snake_case(arg_name):
                self.issues.append((
                    getattr(arg, "lineno", getattr(node, "lineno", "?")),
                    f"Argument '{arg_name}' should be snake_case",
                ))

    def _check_assignment_target(self, target, lineno, context=None):
        if isinstance(target, ast.Name):
            self._check_name(target.id, lineno, context=context)
            return

        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._check_assignment_target(
                    element, lineno, context=context
                )
            return

        if isinstance(target, ast.Attribute):
            self._check_attribute(target, lineno, context=context)
            return

        if isinstance(target, ast.Starred):
            self._check_assignment_target(
                target.value, lineno, context=context
            )

    def _check_name(self, name, lineno, context=None):
        label = context or self._infer_name_label(name)

        if self._is_constant_name(name):
            if not check_upper_case(name):
                self.issues.append((
                    lineno,
                    f"{label} '{name}' should be UPPER_CASE",
                ))
        else:
            if not check_snake_case(name):
                self.issues.append((
                    lineno,
                    f"{label} '{name}' should be snake_case",
                ))

    def _check_attribute(self, target, lineno, context=None):
        attr_name = target.attr
        label = context or self._infer_name_label(
            attr_name, default_label="Attribute"
        )

        if self._is_constant_name(attr_name):
            if not check_upper_case(attr_name):
                self.issues.append((
                    lineno,
                    f"{label} '{attr_name}' should be UPPER_CASE",
                ))
        else:
            if not check_snake_case(attr_name):
                self.issues.append((
                    lineno,
                    f"{label} '{attr_name}' should be snake_case",
                ))

    def _is_constant_name(self, name):
        return name.isupper()

    def _infer_name_label(self, name, default_label="Variable"):
        if name.isupper():
            return "Constant"
        return default_label


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Check naming conventions in project Python libraries and recipes.

    Preserve the original validation rules and configured severity.
    Stop and return error() if reading or parsing fails.
    """

    MAX_REPORTED_ISSUES = 50

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
            severity = int(self.config["severity"])

            results = {
                "analyzed_library_file_count": 0,
                "analyzed_recipe_count": 0,
                "issue_count": 0,
                "issues": [],
            }

            self._scan_library(results)
            self._scan_python_recipes(results)

            results["analyzed_file_count"] = (
                results["analyzed_library_file_count"]
                + results["analyzed_recipe_count"]
            )
            results["reported_issue_count"] = len(results["issues"])
            results["issues_truncated"] = (
                results["issue_count"]
                > results["reported_issue_count"]
            )

            if results["issue_count"]:
                return ProjectStandardsCheckRunResult.failure(
                    severity=severity,
                    message=(
                        "Naming convention issues found: "
                        f"{results['issue_count']} issue(s)."
                    ),
                    details=results,
                )

            return ProjectStandardsCheckRunResult.success(
                message="All naming conventions are respected.",
                details=results,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=f"Error while checking naming conventions: {exc}"
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

            # Exclude before listing folders or reading files.
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

            # Keep __init__.py included, as in the original.
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
        """Count all issues and retain at most 50 examples globally."""
        tree = ast.parse(code, filename=source_name)

        remaining_slots = (
            self.MAX_REPORTED_ISSUES - len(results["issues"])
        )
        visitor = StyleVisitor(max_reported_issues=remaining_slots)
        visitor.visit(tree)

        results["issue_count"] += visitor.issues.total_count

        for lineno, message in visitor.issues:
            results["issues"].append({
                "source": source_name,
                "line": lineno,
                "issue": message,
            })