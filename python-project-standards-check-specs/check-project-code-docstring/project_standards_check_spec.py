import ast
from pathlib import PurePosixPath

from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """Check docstrings in project-owned Python libraries and recipes."""

    # Exact directory names, matched at any depth.
    EXCLUDED_DIRS = frozenset({
        ".ven", "ven", ".venv", "venv", "env", ".env",
        ".git", ".hg", ".svn",
        "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", ".tox", ".nox",
        ".idea", ".vscode",
        "node_modules", "site-packages",
        "dist", "build", "vendor", "third_party",
    })

    MAX_REPORTED_ISSUES = 50
    MAX_REPORTED_ERRORS = 10

    # Protect AST parsing from unusually large source files.
    # This limit applies AFTER reading the file through the DSS API.
    MAX_SOURCE_BYTES = 1_000_000

    def run(self):
        stats = {
            "analyzed_library_files": 0,
            "analyzed_python_recipes": 0,
            "excluded_library_items": 0,
            "missing_docstring_count": 0,
            "unreadable_source_count": 0,
            "missing_docstrings": [],
            "unreadable_sources": [],
        }

        try:
            severity = int(self.config["severity"])

            self._scan_library(stats)
            self._scan_python_recipes(stats)

            stats["issues_truncated"] = (
                stats["missing_docstring_count"]
                > len(stats["missing_docstrings"])
            )
            stats["errors_truncated"] = (
                stats["unreadable_source_count"]
                > len(stats["unreadable_sources"])
            )

            # Never report success after an incomplete scan.
            if stats["unreadable_source_count"]:
                return ProjectStandardsCheckRunResult.error(
                    message=(
                        "Docstring scan incomplete: "
                        f"{stats['unreadable_source_count']} source(s) "
                        "could not be analyzed. "
                        f"{stats['missing_docstring_count']} missing "
                        "docstring(s) found in analyzed sources."
                    ),
                    details=stats,
                )

            if stats["missing_docstring_count"]:
                return ProjectStandardsCheckRunResult.failure(
                    severity=severity,
                    message=(
                        f"{stats['missing_docstring_count']} missing "
                        "docstring(s). Reporting up to "
                        f"{self.MAX_REPORTED_ISSUES} examples."
                    ),
                    details=stats,
                )

            return ProjectStandardsCheckRunResult.success(
                message=(
                    "No missing docstrings found in "
                    f"{stats['analyzed_library_files']} library file(s) "
                    "and "
                    f"{stats['analyzed_python_recipes']} Python recipe(s)."
                ),
                details=stats,
            )

        except Exception as exc:
            # Avoid returning an unbounded exception message.
            return ProjectStandardsCheckRunResult.error(
                message=(
                    "Unable to complete docstring check "
                    f"({type(exc).__name__})."
                ),
                details=stats,
            )

    def _scan_library(self, stats):
        """Walk library items without entering excluded directories."""
        library = self.project.get_library()
        pending = list(library.list())

        # Iterative traversal avoids recursion depth issues.
        while pending:
            item = pending.pop()
            path = str(getattr(item, "path", ""))
            normalized = path.replace("\\", "/")
            parts = PurePosixPath(normalized).parts

            # IMPORTANT: exclude BEFORE listing or reading the item.
            if any(part in self.EXCLUDED_DIRS for part in parts):
                stats["excluded_library_items"] += 1
                continue

            if callable(getattr(item, "list", None)):
                try:
                    pending.extend(item.list())
                except Exception as exc:
                    self._record_error(path, type(exc).__name__, stats)
                continue

            if not normalized.endswith(".py"):
                continue

            # Preserve your original rule.
            if PurePosixPath(normalized).name == "__init__.py":
                continue

            try:
                code = item.read()
                self._analyze_python_code(code, path, stats)
            except Exception as exc:
                self._record_error(path, type(exc).__name__, stats)
            else:
                stats["analyzed_library_files"] += 1

    def _scan_python_recipes(self, stats):
        """Analyze only Python recipe payloads."""
        for recipe_info in self.project.list_recipes():
            if recipe_info.get("type") != "python":
                continue

            name = recipe_info.get("name")
            source = f"recipe:{name}"

            if not name:
                self._record_error(source, "MissingRecipeName", stats)
                continue

            try:
                recipe = self.project.get_recipe(name)
                code = recipe.get_settings().get_payload() or ""
                self._analyze_python_code(code, source, stats)
            except Exception as exc:
                self._record_error(source, type(exc).__name__, stats)
            else:
                stats["analyzed_python_recipes"] += 1

    def _analyze_python_code(self, code, source, stats):
        """Count every violation while retaining a bounded sample."""
        if isinstance(code, bytes):
            if len(code) > self.MAX_SOURCE_BYTES:
                raise ValueError("SourceTooLarge")
            code = code.decode("utf-8")
        elif isinstance(code, str):
            if (
                len(code) > self.MAX_SOURCE_BYTES
                or len(code.encode("utf-8")) > self.MAX_SOURCE_BYTES
            ):
                raise ValueError("SourceTooLarge")
        else:
            raise TypeError("UnsupportedSourceType")

        tree = ast.parse(code, filename=source)

        for node in ast.walk(tree):
            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                continue

            if ast.get_docstring(node) is not None:
                continue

            stats["missing_docstring_count"] += 1

            if (
                len(stats["missing_docstrings"])
                >= self.MAX_REPORTED_ISSUES
            ):
                continue

            stats["missing_docstrings"].append({
                "source": source[:300],
                "line": node.lineno,
                "type": type(node).__name__,
                "name": node.name[:150],
            })

    def _record_error(self, source, reason, stats):
        """Count errors and retain only a small diagnostic sample."""
        stats["unreadable_source_count"] += 1

        if len(stats["unreadable_sources"]) < self.MAX_REPORTED_ERRORS:
            stats["unreadable_sources"].append({
                "source": source[:300],
                "reason": reason[:100],
            })