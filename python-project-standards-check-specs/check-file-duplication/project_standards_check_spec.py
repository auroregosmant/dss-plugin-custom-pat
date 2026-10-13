import ast
import hashlib
from pathlib import PurePosixPath

from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Detect duplicate Python files using normalized AST hashes.

    Exclude dependency folders and __init__.py files.
    Count every duplicate but retain only a limited report.
    Stop and return error() if a source cannot be read or parsed.
    """

    MAX_REPORTED_DUPLICATES = 50

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
            # Keep the original configured severity.
            severity = int(self.config["severity"])

            results = self._scan_library_for_duplicates()
            duplicate_count = results["duplicate_count"]

            if duplicate_count:
                return ProjectStandardsCheckRunResult.failure(
                    severity=severity,
                    message=(
                        "Duplicate Python files found in lib/: "
                        f"{duplicate_count} duplicate pair(s)."
                    ),
                    details=results,
                )

            return ProjectStandardsCheckRunResult.success(
                message="No duplicate Python files found in lib/.",
                details=results,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=(
                    "Error while checking duplicate Python files "
                    f"in lib/: {exc}"
                )
            )

    def _scan_library_for_duplicates(self):
        """Scan eligible files and retain one path per unique hash."""
        try:
            library = self.project.get_library()
            items = library.list()
        except Exception as exc:
            raise RuntimeError(
                "Error accessing project library: "
                f"{type(exc).__name__}"
            ) from None

        results = {
            "analyzed_file_count": 0,
            "duplicate_count": 0,
            "reported_duplicate_count": 0,
            "duplicates_truncated": False,
            "duplicates": [],
        }
        file_hashes = {}

        self._walk_library_items(items, file_hashes, results)

        results["reported_duplicate_count"] = len(results["duplicates"])
        results["duplicates_truncated"] = (
            results["duplicate_count"]
            > results["reported_duplicate_count"]
        )

        return results

    def _walk_library_items(self, items, file_hashes, results):
        """Exclude folders before listing or reading their contents."""
        for item in items:
            path = str(getattr(item, "path", ""))
            normalized_path = path.replace("\\", "/")
            parts = PurePosixPath(normalized_path).parts

            # IMPORTANT: filter before item.list() or item.read().
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

                self._walk_library_items(
                    children, file_hashes, results
                )
                continue

            # Preserve the original file-selection rule.
            if (
                not normalized_path.endswith(".py")
                or normalized_path.endswith("__init__.py")
            ):
                continue

            try:
                content = item.read() or ""
            except Exception as exc:
                raise RuntimeError(
                    f"Error reading library file '{path}': "
                    f"{type(exc).__name__}"
                ) from None

            try:
                normalized_hash = self._hash_normalized_python(content)
            except Exception as exc:
                raise RuntimeError(
                    f"Error parsing Python file '{path}': "
                    f"{type(exc).__name__}"
                ) from None

            results["analyzed_file_count"] += 1

            if normalized_hash in file_hashes:
                results["duplicate_count"] += 1

                if (
                    len(results["duplicates"])
                    < self.MAX_REPORTED_DUPLICATES
                ):
                    results["duplicates"].append({
                        "original": file_hashes[normalized_hash],
                        "duplicate": path,
                        "hash": normalized_hash,
                    })
            else:
                file_hashes[normalized_hash] = path

    def _hash_normalized_python(self, content):
        """Preserve the original normalized AST and MD5 calculation."""
        tree = ast.parse(content)
        normalized = ast.dump(
            tree,
            annotate_fields=True,
            include_attributes=False,
        )
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()