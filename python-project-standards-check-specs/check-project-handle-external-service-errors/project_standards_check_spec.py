import ast
from pathlib import PurePosixPath

from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class BoundedIssues(list):
    """Count every issue while retaining a limited number of examples."""

    def __init__(self, limit=50):
        super().__init__()
        self.limit = limit
        self.total_count = 0

    def append(self, issue):
        self.total_count += 1
        if len(self) < self.limit:
            super().append(issue)


class ParentNodeTransformer(ast.NodeTransformer):
    """Attach parent references to AST nodes."""

    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.visit(child)
        return node


class ExternalServiceVisitor(ast.NodeVisitor):
    """
    Detect requests/httpx calls and check their exception handling.

    Detection rules are preserved from the original check.
    """

    HTTP_METHODS = {
        "get", "post", "put", "delete", "patch",
        "head", "options", "request", "send", "stream",
    }

    CLIENT_CLASSES = {"Client", "AsyncClient", "Session"}

    REQUESTS_EXCEPTION_NAMES = {
        "RequestException", "HTTPError", "ConnectionError",
        "Timeout", "TooManyRedirects",
    }

    HTTPX_EXCEPTION_NAMES = {
        "HTTPError", "RequestError", "TransportError",
        "TimeoutException", "ConnectError", "ReadError",
        "WriteError", "NetworkError", "ProtocolError",
    }

    def __init__(self, max_reported_issues=50):
        self.issues = BoundedIssues(limit=max_reported_issues)
        self.requests_aliases = set()
        self.httpx_aliases = set()
        self.client_aliases = set()
        self.requests_exception_aliases = set()
        self.httpx_exception_aliases = set()

    def visit_Import(self, node):
        for alias in node.names:
            local_name = alias.asname or alias.name

            if alias.name == "requests":
                self.requests_aliases.add(local_name)

            if alias.name == "httpx":
                self.httpx_aliases.add(local_name)

        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""

        if module == "requests":
            for alias in node.names:
                imported_name = alias.name
                local_name = alias.asname or alias.name

                if imported_name in self.HTTP_METHODS:
                    self.requests_aliases.add(local_name)

                if imported_name in self.CLIENT_CLASSES:
                    self.client_aliases.add(local_name)

                if imported_name in self.REQUESTS_EXCEPTION_NAMES:
                    self.requests_exception_aliases.add(local_name)

        if module == "httpx":
            for alias in node.names:
                imported_name = alias.name
                local_name = alias.asname or alias.name

                if imported_name in self.HTTP_METHODS:
                    self.httpx_aliases.add(local_name)

                if imported_name in self.CLIENT_CLASSES:
                    self.client_aliases.add(local_name)

                if imported_name in self.HTTPX_EXCEPTION_NAMES:
                    self.httpx_exception_aliases.add(local_name)

        self.generic_visit(node)

    def visit_Assign(self, node):
        """Track client = httpx.Client() and session = requests.Session()."""
        if (
            isinstance(node.value, ast.Call)
            and self._is_client_constructor(node.value.func)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.client_aliases.add(target.id)

        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name):
            if (
                isinstance(node.value, ast.Call)
                and self._is_client_constructor(node.value.func)
            ):
                self.client_aliases.add(node.target.id)

        self.generic_visit(node)

    def visit_Call(self, node):
        call_type = self._get_external_call_type(node)

        if call_type:
            try_node = self._get_enclosing_try(node)

            if try_node is None:
                self.issues.append((
                    getattr(node, "lineno", "?"),
                    f"External service call "
                    f"'{self._get_call_representation(node)}' "
                    "is not wrapped in try/except",
                ))

            elif not self._try_handles_expected_exceptions(
                try_node, call_type
            ):
                expected = (
                    "requests.RequestException"
                    if call_type == "requests"
                    else "httpx.HTTPError / httpx.RequestError"
                )

                self.issues.append((
                    getattr(node, "lineno", "?"),
                    f"External service call "
                    f"'{self._get_call_representation(node)}' "
                    "is inside try/except, but the except block "
                    "does not catch "
                    f"the expected {call_type} exception(s) ({expected})",
                ))

        self.generic_visit(node)

    def _get_external_call_type(self, node):
        func = node.func

        if isinstance(func, ast.Attribute) and func.attr in self.HTTP_METHODS:
            if isinstance(func.value, ast.Name):
                base_name = func.value.id

                if base_name in self.requests_aliases:
                    return "requests"

                if base_name in self.httpx_aliases:
                    return "httpx"

                if base_name in self.client_aliases:
                    return "client"

        if isinstance(func, ast.Name):
            if func.id in self.requests_aliases:
                return "requests"

            if func.id in self.httpx_aliases:
                return "httpx"

        return None

    def _is_client_constructor(self, func):
        if isinstance(func, ast.Attribute):
            if (
                isinstance(func.value, ast.Name)
                and func.value.id
                in self.requests_aliases.union(self.httpx_aliases)
                and func.attr in self.CLIENT_CLASSES
            ):
                return True

        if isinstance(func, ast.Name) and func.id in self.client_aliases:
            return True

        return False

    def _get_enclosing_try(self, node):
        current = getattr(node, "parent", None)

        while current is not None:
            if isinstance(current, ast.Try):
                return current
            current = getattr(current, "parent", None)

        return None

    def _try_handles_expected_exceptions(self, try_node, call_type):
        for handler in try_node.handlers:
            if self._handler_matches_call_type(handler, call_type):
                return True
        return False

    def _handler_matches_call_type(self, handler, call_type):
        if handler.type is None:
            return True

        exception_names = self._extract_exception_names(handler.type)

        if not exception_names:
            return False

        if "Exception" in exception_names or "BaseException" in exception_names:
            return True

        if call_type == "requests":
            valid_names = (
                self.REQUESTS_EXCEPTION_NAMES
                .union({
                    f"requests.{name}"
                    for name in self.REQUESTS_EXCEPTION_NAMES
                })
                .union(self.requests_exception_aliases)
            )

        elif call_type == "httpx":
            valid_names = (
                self.HTTPX_EXCEPTION_NAMES
                .union({
                    f"httpx.{name}"
                    for name in self.HTTPX_EXCEPTION_NAMES
                })
                .union(self.httpx_exception_aliases)
            )

        elif call_type == "client":
            valid_names = (
                self.REQUESTS_EXCEPTION_NAMES
                .union({
                    f"requests.{name}"
                    for name in self.REQUESTS_EXCEPTION_NAMES
                })
                .union(self.requests_exception_aliases)
                .union(self.HTTPX_EXCEPTION_NAMES)
                .union({
                    f"httpx.{name}"
                    for name in self.HTTPX_EXCEPTION_NAMES
                })
                .union(self.httpx_exception_aliases)
            )

        else:
            return False

        return any(name in valid_names for name in exception_names)

    def _extract_exception_names(self, exc_node):
        names = set()

        if isinstance(exc_node, ast.Name):
            names.add(exc_node.id)

        elif isinstance(exc_node, ast.Attribute):
            names.add(self._attribute_to_name(exc_node))
            names.add(exc_node.attr)

        elif isinstance(exc_node, ast.Tuple):
            for element in exc_node.elts:
                names.update(self._extract_exception_names(element))

        return names

    def _attribute_to_name(self, node):
        parts = []
        current = node

        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value

        if isinstance(current, ast.Name):
            parts.append(current.id)

        return ".".join(reversed(parts))

    def _get_call_representation(self, node):
        func = node.func

        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                return f"{func.value.id}.{func.attr}(...)"
            return f"{func.attr}(...)"

        if isinstance(func, ast.Name):
            return f"{func.id}(...)"

        return "external_call(...)"


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """Check exception handling for requests/httpx calls."""

    MAX_REPORTED_ISSUES = 50

    # Exact directory names, excluded at any depth.
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

            library_files, library_issues = self._scan_library()
            recipe_files, recipe_issues = self._scan_python_recipes()

            analyzed_file_count = len(library_files) + len(recipe_files)
            issue_count = (
                library_issues.total_count + recipe_issues.total_count
            )

            reported_issues = (
                list(library_issues) + list(recipe_issues)
            )[:self.MAX_REPORTED_ISSUES]

            details = {
                "analyzed_file_count": analyzed_file_count,
                "issue_count": issue_count,
                "reported_issue_count": len(reported_issues),
                "issues_truncated": issue_count > len(reported_issues),
                "issues": reported_issues,
            }

            if issue_count:
                return ProjectStandardsCheckRunResult.failure(
                    severity=severity,
                    message=(
                        "Potential unhandled HTTPS/external service "
                        f"calls found: {issue_count} issue(s)."
                    ),
                    details=details,
                )

            return ProjectStandardsCheckRunResult.success(
                message=(
                    "All HTTPS/external service calls appear "
                    "to handle errors properly."
                ),
                details=details,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=(
                    "Error while checking external service "
                    f"error handling: {exc}"
                )
            )

    def _scan_library(self):
        """Scan project Python files outside excluded folders."""
        try:
            library = self.project.get_library()
            items = library.list()
        except Exception as exc:
            raise RuntimeError(
                f"Error accessing project library: {exc}"
            ) from None

        analyzed_files = []
        issues = BoundedIssues(limit=self.MAX_REPORTED_ISSUES)

        self._walk_library_items(items, analyzed_files, issues)

        return analyzed_files, issues

    def _walk_library_items(self, items, analyzed_files, issues):
        for item in items:
            path = str(getattr(item, "path", ""))
            normalized_path = path.replace("\\", "/")
            parts = PurePosixPath(normalized_path).parts

            # Skip excluded items BEFORE listing or reading them.
            if any(part in self.EXCLUDED_DIRS for part in parts):
                continue

            if callable(getattr(item, "list", None)):
                try:
                    self._walk_library_items(
                        item.list(), analyzed_files, issues
                    )
                except Exception:
                    # Avoid repeatedly wrapping nested folder errors.
                    raise
                continue

            if not normalized_path.endswith(".py"):
                continue

            # __init__.py remains included, as in the original check.
            analyzed_files.append(path)

            try:
                content = item.read()
                self._analyze_python_code(
                    code=content,
                    source_name=path,
                    issues=issues,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Error reading/parsing library file '{path}': "
                    f"{type(exc).__name__}"
                ) from None

    def _scan_python_recipes(self):
        """Scan all Python recipes, as in the original check."""
        analyzed_files = []
        issues = BoundedIssues(limit=self.MAX_REPORTED_ISSUES)

        try:
            recipes = self.project.list_recipes()
        except Exception as exc:
            raise RuntimeError(
                f"Error listing project recipes: {exc}"
            ) from None

        for recipe_info in recipes:
            if recipe_info.get("type") != "python":
                continue

            recipe_name = recipe_info.get("name")
            source_name = f"recipe:{recipe_name}"
            analyzed_files.append(source_name)

            try:
                recipe = self.project.get_recipe(recipe_name)
                code = recipe.get_settings().get_payload() or ""

                self._analyze_python_code(
                    code=code,
                    source_name=source_name,
                    issues=issues,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Error reading/parsing Python recipe '{recipe_name}': "
                    f"{type(exc).__name__}"
                ) from None

        return analyzed_files, issues

    def _analyze_python_code(self, code, source_name, issues):
        """Apply the original detection rules with bounded reporting."""
        tree = ast.parse(code, filename=source_name)
        ParentNodeTransformer().visit(tree)

        visitor = ExternalServiceVisitor(
            max_reported_issues=self.MAX_REPORTED_ISSUES
        )
        visitor.visit(tree)

        for lineno, message in visitor.issues:
            issues.append({
                "source": source_name,
                "line": lineno,
                "issue": message,
            })

        # Count findings not retained in the visitor's sample.
        issues.total_count += (
            visitor.issues.total_count - len(visitor.issues)
        )