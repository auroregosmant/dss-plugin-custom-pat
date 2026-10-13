import ast
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

    This check scans both the project library and Python recipes to verify
    that MLflow or Dataiku model tracking is used.

    Rules:
    - success(): MLflow or Dataiku tracking is detected at least once
    - failure(severity=...): no MLflow or Dataiku tracking is detected
    - error(): the check itself cannot run
    """

    TRACKING_METHODS = {
        "log_param",
        "log_params",
        "log_metric",
        "log_metrics",
        "log_artifact",
        "log_artifacts",
        "log_model",
        "autolog",
        "save_version",
        "train",
    }

    def run(self):
        try:
            severity = int(self.config["severity"])

            (
                library_files,
                library_mlflow_used,
                library_dss_tracking_used,
                library_suspicious_models,
            ) = self._scan_library()

            (
                recipe_files,
                recipe_mlflow_used,
                recipe_dss_tracking_used,
                recipe_suspicious_models,
            ) = self._scan_python_recipes()

            analyzed_files = library_files + recipe_files
            mlflow_used = library_mlflow_used or recipe_mlflow_used
            dss_tracking_used = library_dss_tracking_used or recipe_dss_tracking_used
            suspicious_models = sorted(
                set(library_suspicious_models + recipe_suspicious_models)
            )

            details = {
                #"analyzed_library_files": library_files,
                #"analyzed_recipe_files": recipe_files,
                "analyzed_file_count": len(analyzed_files),
                "mlflow_used": mlflow_used,
                "dss_tracking_used": dss_tracking_used,
                "suspicious_models": suspicious_models,
            }

            if mlflow_used or dss_tracking_used:
                message = "Experiment tracking detected with MLflow or Dataiku."
                if suspicious_models:
                    message += (
                        " Suspicious model training calls were also found in: "
                        + ", ".join(suspicious_models)
                    )

                return ProjectStandardsCheckRunResult.success(
                    message=message,
                    details=details,
                )

            message = "No MLflow or Dataiku tracking detected."
            if suspicious_models:
                message += (
                    " Suspicious model training without tracking found in: "
                    + ", ".join(suspicious_models)
                )

            return ProjectStandardsCheckRunResult.failure(
                severity=severity,
                message=message,
                details=details,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=f"Error while checking MLflow/Dataiku tracking usage: {exc}"
            )

    def _scan_library(self):
        """
        Scan Python files in the project library.
        """
        try:
            library = self.project.get_library()
            items = library.list()
        except Exception as exc:
            raise RuntimeError(f"Error accessing project library: {exc}")

        analyzed_files = []
        mlflow_used = False
        dss_tracking_used = False
        suspicious_models = []

        (
            analyzed_files,
            mlflow_used,
            dss_tracking_used,
            suspicious_models,
        ) = self._walk_library_items(items)

        return analyzed_files, mlflow_used, dss_tracking_used, suspicious_models

    def _walk_library_items(self, items):
        analyzed_files = []
        mlflow_used = False
        dss_tracking_used = False
        suspicious_models = []

        for item in items:
            if hasattr(item, "list"):
                try:
                    (
                        child_files,
                        child_mlflow,
                        child_dss,
                        child_suspicious,
                    ) = self._walk_library_items(item.list())
                except Exception as exc:
                    raise RuntimeError(f"Error listing folder contents: {exc}")

                analyzed_files.extend(child_files)
                mlflow_used = mlflow_used or child_mlflow
                dss_tracking_used = dss_tracking_used or child_dss
                suspicious_models.extend(child_suspicious)
                continue

            path = getattr(item, "path", "")
            if not path.endswith(".py"):
                continue

            analyzed_files.append(path)

            try:
                code = item.read()
                file_mlflow, file_dss, file_suspicious = self._analyze_python_code(
                    code=code,
                    source_name=path,
                )
            except Exception as exc:
                raise RuntimeError(f"Error reading/parsing library file '{path}': {exc}")

            mlflow_used = mlflow_used or file_mlflow
            dss_tracking_used = dss_tracking_used or file_dss
            suspicious_models.extend(file_suspicious)

        return analyzed_files, mlflow_used, dss_tracking_used, suspicious_models

    def _scan_python_recipes(self):
        """
        Scan Python recipes in the project.
        """
        analyzed_files = []
        mlflow_used = False
        dss_tracking_used = False
        suspicious_models = []

        try:
            recipes = self.project.list_recipes()
        except Exception as exc:
            raise RuntimeError(f"Error listing project recipes: {exc}")

        for recipe_info in recipes:
            if recipe_info.get("type") != "python":
                continue

            recipe_name = recipe_info.get("name")
            source_name = f"recipe:{recipe_name}"
            analyzed_files.append(source_name)

            try:
                recipe = self.project.get_recipe(recipe_name)
                code = recipe.get_settings().get_payload() or ""
                file_mlflow, file_dss, file_suspicious = self._analyze_python_code(
                    code=code,
                    source_name=source_name,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Error reading/parsing Python recipe '{recipe_name}': {exc}"
                )

            mlflow_used = mlflow_used or file_mlflow
            dss_tracking_used = dss_tracking_used or file_dss
            suspicious_models.extend(file_suspicious)

        return analyzed_files, mlflow_used, dss_tracking_used, suspicious_models

    def _analyze_python_code(self, code, source_name):
        """
        Parse Python code and detect:
        - MLflow usage
        - Dataiku tracking usage
        - suspicious model training calls without explicit tracking in the same file
        """
        tree = ast.parse(code, filename=source_name)

        mlflow_used = False
        dss_tracking_used = False
        has_training_call = False

        # Imports
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("mlflow"):
                        mlflow_used = True
                    if alias.name == "dataiku":
                        dss_tracking_used = True

            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("mlflow"):
                    mlflow_used = True
                if node.module and node.module == "dataiku":
                    dss_tracking_used = True

        # Function / method calls
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if self._is_mlflow_call(node):
                mlflow_used = True

            if self._is_dss_tracking_call(node):
                dss_tracking_used = True

            if self._is_training_call(node):
                has_training_call = True

        suspicious_models = []
        if has_training_call and not (mlflow_used or dss_tracking_used):
            suspicious_models.append(source_name)

        return mlflow_used, dss_tracking_used, suspicious_models

    def _is_mlflow_call(self, node):
        """
        Detect calls such as:
        - mlflow.log_param(...)
        - mlflow.log_metric(...)
        - mlflow.sklearn.log_model(...)
        - mlflow.autolog(...)
        """
        func = node.func

        if isinstance(func, ast.Attribute):
            if func.attr in self.TRACKING_METHODS:
                current = func
                while isinstance(current, ast.Attribute):
                    current = current.value
                return isinstance(current, ast.Name) and current.id == "mlflow"

        return False

    def _is_dss_tracking_call(self, node):
        """
        Detect simplified Dataiku tracking patterns such as:
        - Model(...)
        - something.save_version(...)
        - something.train(...)
        """
        func = node.func

        if isinstance(func, ast.Name) and func.id == "Model":
            return True

        if isinstance(func, ast.Attribute) and func.attr in {"save_version", "train"}:
            return True

        return False

    def _is_training_call(self, node):
        """
        Detect generic training calls such as:
        - model.fit(...)
        - model.train(...)
        """
        func = node.func
        return isinstance(func, ast.Attribute) and func.attr in {"fit", "train"}