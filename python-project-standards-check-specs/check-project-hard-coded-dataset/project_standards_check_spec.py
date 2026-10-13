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

    This check detects datasets whose table name appears to be hard-coded.
    A table is considered hard-coded when:
    - dataset["params"]["table"] exists
    - and does not contain '$'
    """

    def run(self):
        try:
            severity = int(self.config["severity"])

            project = self.project
            all_datasets = project.list_datasets()
            all_recipes = project.list_recipes()

            output_dataset_names = self._get_output_dataset_names(all_recipes)

            # Datasets used as outputs of recipes
            datasets_with_inputs = [
                dataset
                for dataset in all_datasets
                if dataset.get("name") in output_dataset_names
            ]

            hard_coded_tables = []
            analyzed_datasets = []

            for dataset in datasets_with_inputs:
                dataset_name = dataset.get("name")
                analyzed_datasets.append(dataset_name)

                table_name = (
                    dataset.get("params", {}).get("table")
                    if isinstance(dataset.get("params", {}), dict)
                    else None
                )

                if table_name and "$" not in table_name:
                    hard_coded_tables.append(
                        {
                            "dataset": dataset_name,
                            "table": table_name,
                            "issue": "Hard-coded table name detected",
                        }
                    )

            details = {
                "analyzed_dataset_count": len(analyzed_datasets),
                "hard_coded_table_count": len(hard_coded_tables),
                "hard_coded_tables": hard_coded_tables,
            }

            if hard_coded_tables:
                return ProjectStandardsCheckRunResult.failure(
                    severity=severity,
                    message=(
                        f"{len(hard_coded_tables)} dataset(s) with hard-coded table names found."
                    ),
                    details=details,
                )

            return ProjectStandardsCheckRunResult.success(
                message="No hard-coded dataset table names found.",
                details=details,
            )

        except Exception as exc:
            return ProjectStandardsCheckRunResult.error(
                message=f"Error while checking hard-coded dataset names: {exc}"
            )

    def _get_output_dataset_names(self, recipes):
        """
        Extract dataset names from recipe outputs.
        """
        output_names = set()

        for recipe in recipes:
            try:
                main_outputs = recipe.get("outputs", {}).get("main", {}).get("items", [])
                for item in main_outputs:
                    ref = item.get("ref")
                    if ref:
                        output_names.add(ref)
            except Exception:
                continue

        return output_names