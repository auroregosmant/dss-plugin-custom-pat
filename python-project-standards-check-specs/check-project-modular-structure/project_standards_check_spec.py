from pathlib import PurePosixPath

from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Verify the project library structure:
    - python/src/ and python/tests/ must exist.
    - Each must contain a Python file other than __init__.py.
    - Python files directly under python/ are forbidden,
      except __init__.py.

    Exclude dependency folders and limit reported file paths.
    """

    REQUIRED_DIRS = ("src", "tests")
    MAX_REPORTED_FILES = 50

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
            # Preserve the original severity.
            severity = int(self.config["severity"])
            analysis = self._scan_library_structure()

            issues = []

            if analysis["missing_dirs"]:
                issues.append(
                    "Missing required directories: "
                    + ", ".join(analysis["missing_dirs"])
                )

            if analysis["empty_dirs"]:
                issues.append(
                    "Required directories without Python files "
                    "other than __init__.py: "
                    + ", ".join(analysis["empty_dirs"])
                )

            if analysis["root_python_file_count"]:
                issues.append(
                    "Python files found directly under python/: "
                    f"{analysis['root_python_file_count']} file(s)"
                )

            if issues:
                return ProjectStandardsCheckRunResult.failure(
                    severity=severity,
                    message=(
                        "Library structure is not compliant. "
                        + " | ".join(issues)
                    ),
                    details=analysis,
                )

            return ProjectStandardsCheckRunResult.success(
                message=(
                    "Library structure is compliant: python/src/ "
                    "and python/tests/ exist, contain Python files "
                    "other than __init__.py, and no other Python files "
                    "are stored directly under python/."
                ),
                details=analysis,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=f"Error while checking library structure: {exc}"
            )

    def _scan_library_structure(self):
        """Inspect paths without reading file contents."""
        try:
            library = self.project.get_library()
            items = library.list()
        except Exception as exc:
            raise RuntimeError(
                "Error accessing project library: "
                f"{type(exc).__name__}"
            ) from None

        analysis = {
            "required_dirs": list(self.REQUIRED_DIRS),
            "dir_to_python_files": {
                name: [] for name in self.REQUIRED_DIRS
            },
            "dir_to_python_file_count": {
                name: 0 for name in self.REQUIRED_DIRS
            },
            "dir_to_non_init_file_count": {
                name: 0 for name in self.REQUIRED_DIRS
            },
            "missing_dirs": [],
            "empty_dirs": [],
            "root_python_files": [],
            "root_python_file_count": 0,
        }

        present_dirs = set()
        self._walk_library_items(items, present_dirs, analysis)

        for name in self.REQUIRED_DIRS:
            if name not in present_dirs:
                analysis["missing_dirs"].append(name)
            elif analysis["dir_to_non_init_file_count"][name] == 0:
                analysis["empty_dirs"].append(name)

        analysis["missing_dirs"].sort()
        analysis["empty_dirs"].sort()
        analysis["root_python_files"].sort()

        for paths in analysis["dir_to_python_files"].values():
            paths.sort()

        analysis["root_files_truncated"] = (
            analysis["root_python_file_count"]
            > len(analysis["root_python_files"])
        )
        analysis["dir_files_truncated"] = {
            name: (
                analysis["dir_to_python_file_count"][name]
                > len(analysis["dir_to_python_files"][name])
            )
            for name in self.REQUIRED_DIRS
        }

        return analysis

    def _walk_library_items(self, items, present_dirs, analysis):
        """Traverse only python/ and its required directories."""
        for item in items:
            path = str(getattr(item, "path", ""))
            clean_path = str(
                PurePosixPath(path.replace("\\", "/"))
            ).lstrip("/")
            parts = PurePosixPath(clean_path).parts

            if any(part in self.EXCLUDED_DIRS for part in parts):
                continue

            # Only python/ is relevant to this structure check.
            if not parts or parts[0] != "python":
                continue

            is_folder = callable(getattr(item, "list", None))

            if is_folder:
                if len(parts) == 2:
                    if parts[1] not in self.REQUIRED_DIRS:
                        continue
                    present_dirs.add(parts[1])

                elif len(parts) > 2:
                    if parts[1] not in self.REQUIRED_DIRS:
                        continue

                try:
                    children = item.list()
                except Exception as exc:
                    raise RuntimeError(
                        f"Error listing folder '{clean_path}': "
                        f"{type(exc).__name__}"
                    ) from None

                self._walk_library_items(
                    children, present_dirs, analysis
                )
                continue

            if not clean_path.endswith(".py"):
                continue

            if len(parts) == 2:
                if parts[-1] != "__init__.py":
                    analysis["root_python_file_count"] += 1

                    if (
                        len(analysis["root_python_files"])
                        < self.MAX_REPORTED_FILES
                    ):
                        analysis["root_python_files"].append(clean_path)
                continue

            if len(parts) < 3 or parts[1] not in self.REQUIRED_DIRS:
                continue

            name = parts[1]
            analysis["dir_to_python_file_count"][name] += 1

            if parts[-1] != "__init__.py":
                analysis["dir_to_non_init_file_count"][name] += 1

            reported_paths = analysis["dir_to_python_files"][name]
            if len(reported_paths) < self.MAX_REPORTED_FILES:
                reported_paths.append(clean_path)