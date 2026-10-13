"""
V1 : How it works
------------
1.  Reads the allowlist CSV from a managed folder in the ADX project.
    (The CSV is synced daily from the Snowflake allowlist table by a scenario.)
2.  Looks up the current project in the allowlist to retrieve:
        - allowed_schemas       (comma-separated)
        - instance_schema_var   (single variable name)
3.  Scans the project for unauthorised schema references in:
        - Project variables     (any WRK* value not in the allowlist)
        - SQL recipes           (three-part table references db.schema.table)
        - Python recipes        (SQLExecutor2 calls with three-part references)
        - Snowflake datasets    (schema field in dataset params)
4.  Returns success / failure / not_applicable accordingly.

Plugin check config parameters
-------------------------------
adx_project_key   (string)  — Project key of the ADX governance project that
                               owns the managed folder.

managed_folder_id (string)  — The *ID* (not display name) of the managed
                               folder inside the ADX project.

allowlist_file    (string)  — Name of the CSV file inside the folder.
                               Default: "schema_allowlist.csv"

severity          (integer) — Failure severity (1 = info … 5 = critical).
                               Default: 3
"""

import csv
import io
import re

import dataiku
from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


# ---------------------------------------------------------------------------
# Pure-logic helpers
# ---------------------------------------------------------------------------

def resolve_variable(var_name, project_variables):
    global_vars = project_variables.get("standard", {})
    local_vars  = project_variables.get("local", {})
    if var_name in global_vars:
        return str(global_vars[var_name]), True, "global"
    if var_name in local_vars:
        return str(local_vars[var_name]), True, "local"
    return None, False, None


def check_schema(schema, project_variables, allowed_schemas, instance_schema_var):
    """
    Evaluate one schema token (raw value or ${VAR_NAME} reference).

    Returns None if the schema is authorised, or a dict describing the
    issue if it is not.
    """
    if schema.startswith("${"):
        var_name = schema[2:-1]
        value, found, section = resolve_variable(var_name, project_variables)

        if found:
            if value in allowed_schemas:
                return None
            return {
                "schema_reference": schema,
                "resolution": f"Resolved to '{value}' (project {section} variable)",
                "reason": f"Value '{value}' is not in allowed schemas",
            }
        else:
            if var_name == instance_schema_var:
                return None
            return {
                "schema_reference": schema,
                "resolution": "Not found in project variables → treated as instance variable",
                "reason": f"Instance variable '{var_name}' ≠ allowed instance var '{instance_schema_var}'",
            }
    else:
        if schema in allowed_schemas:
            return None
        return {
            "schema_reference": schema,
            "resolution": "Hardcoded value",
            "reason": f"'{schema}' is not in allowed schemas",
        }


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------

def detect_schemas_in_sql_recipes(project, sql_recipes, project_variables,
                                   allowed_schemas, instance_schema_var):
    results = []
    for recipe_meta in sql_recipes:
        recipe_name = recipe_meta["name"]
        recipe_obj  = project.get_recipe(recipe_name)
        settings    = recipe_obj.get_settings()
        code        = settings.data.get("payload", "")

        for match in re.findall(r'([$\w{}]+)\.([$\w{}]+)\.([$\w{}]+)', code):
            schema = match[1]
            issue  = check_schema(schema, project_variables, allowed_schemas, instance_schema_var)
            if issue:
                results.append({
                    "type":             "SQL Recipe",
                    "name":             recipe_name,
                    "schema_reference": issue["schema_reference"],
                    "resolution":       issue["resolution"],
                    "reason":           issue["reason"],
                    "detail":           ".".join(match),
                })
    return results


def detect_schemas_in_python_recipes(project, python_recipes, project_variables,
                                      allowed_schemas, instance_schema_var):
    results = []
    for recipe_meta in python_recipes:
        recipe_name = recipe_meta["name"]
        recipe_obj  = project.get_recipe(recipe_name)
        settings    = recipe_obj.get_settings()
        code        = settings.data.get("payload", "")

        if "SQLExecutor2" not in code:
            continue

        for match in re.findall(r'([$\w{}]+)\.([$\w{}]+)\.([$\w{}]+)', code):
            schema = match[1]
            issue  = check_schema(schema, project_variables, allowed_schemas, instance_schema_var)
            if issue:
                results.append({
                    "type":             "Python Recipe (SQLExecutor2)",
                    "name":             recipe_name,
                    "schema_reference": issue["schema_reference"],
                    "resolution":       issue["resolution"],
                    "reason":           issue["reason"],
                    "detail":           ".".join(match),
                })
    return results


def detect_schemas_in_datasets(project, sf_datasets, project_variables,
                                allowed_schemas, instance_schema_var):
    results = []
    for ds in sf_datasets:
        ds_name = ds.get("name")
        schema  = str(ds.get("params", {}).get("schema", ""))
        issue   = check_schema(schema, project_variables, allowed_schemas, instance_schema_var)
        if issue:
            results.append({
                "type":             "Snowflake Dataset",
                "name":             ds_name,
                "schema_reference": issue["schema_reference"],
                "resolution":       issue["resolution"],
                "reason":           issue["reason"],
                "detail":           "",
            })
    return results


def detect_wrk_schemas_in_variables(project, project_variables, allowed_schemas):
    results = []
    sections = {
        "global": project_variables.get("standard", {}).items(),
        "local":  project_variables.get("local", {}).items(),
    }
    for section_name, items in sections.items():
        for var_name, var_value in items:
            str_value = str(var_value)
            if str_value.startswith("WRK") and str_value not in allowed_schemas:
                results.append({
                    "type":             f"Project Variable ({section_name})",
                    "name":             var_name,
                    "schema_reference": str_value,
                    "resolution":       f"Direct variable value (project {section_name})",
                    "reason":           f"'{str_value}' is not in allowed schemas",
                    "detail":           "",
                })
    return results


# ---------------------------------------------------------------------------
# Allowlist reader
# ---------------------------------------------------------------------------

def read_allowlist_from_folder(adx_project_key, managed_folder_id, allowlist_file):
    """
    Read the allowlist CSV from a managed folder and return a dict:
        { "PROJECT_KEY": {"allowed_schemas": [...], "instance_schema_var": "..."}, ... }

    Raises RuntimeError with a descriptive message on any failure so the
    check can surface it as an error result.
    """
    client = dataiku.api_client()

    try:
        adx_project = client.get_project(adx_project_key)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot access ADX project '{adx_project_key}': {exc}"
        )

    try:
        folder = adx_project.get_managed_folder(managed_folder_id)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot access managed folder '{managed_folder_id}' "
            f"in project '{adx_project_key}': {exc}"
        )

    try:
        with folder.get_file(allowlist_file) as f:
            raw = f.read().decode("utf-8")
    except Exception as exc:
        raise RuntimeError(
            f"Cannot read file '{allowlist_file}' from managed folder "
            f"'{managed_folder_id}': {exc}"
        )

    allowlist = {}
    reader = csv.DictReader(io.StringIO(raw))

    required_columns = {"project_key", "allowed_schemas", "instance_schema_var"}
    if not required_columns.issubset(set(reader.fieldnames or [])):
        raise RuntimeError(
            f"Allowlist CSV is missing required columns. "
            f"Expected {required_columns}, got {reader.fieldnames}"
        )

    for row in reader:
        key = row["project_key"].strip().upper()
        schemas = [s.strip().upper() for s in row["allowed_schemas"].split(",") if s.strip()]
        instance_var = row["instance_schema_var"].strip()
        allowlist[key] = {
            "allowed_schemas":     schemas,
            "instance_schema_var": instance_var,
        }

    return allowlist


# ---------------------------------------------------------------------------
# Main check class
# ---------------------------------------------------------------------------

class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Snowflake Schema Compliance Check
    ----------------------------------
    Verifies that a project only consumes Snowflake schemas it is
    authorised to use, according to the central allowlist stored in a
    managed folder in the ADX project.

    """

    DEFAULT_ALLOWLIST_FILE = "schema_allowlist.csv"
    DEFAULT_SEVERITY       = 3

    def run(self):
        # ------------------------------------------------------------------ #
        # 1. Configurations
        # ------------------------------------------------------------------ #
        adx_project_key   = "ADX"
        managed_folder_id = ""
        allowlist_file    = self.DEFAULT_ALLOWLIST_FILE
        severity          = self.DEFAULT_SEVERITY
        

        project     = self.project
        project_key = project.project_key

        # ------------------------------------------------------------------ #
        # 2. Load the allowlist from the managed folder
        # ------------------------------------------------------------------ #
        try:
            allowlist = read_allowlist_from_folder(
                adx_project_key, managed_folder_id, allowlist_file
            )
        except RuntimeError as exc:
            return ProjectStandardsCheckRunResult.error(message=str(exc))

        # ------------------------------------------------------------------ #
        # 3. Look up this project in the allowlist : Project Not Present in the list
        # ------------------------------------------------------------------ #
        if project_key not in allowlist:
            return ProjectStandardsCheckRunResult.not_applicable(
                message=(
                    f"Project '{project_key}' is not listed in the schema allowlist "
                    f"(file: '{allowlist_file}'). "
                    f"Add it to the allowlist to enable this check."
                )
            )

            
        allowed_schemas     = allowlist[project_key]["allowed_schemas"]
        instance_schema_var = allowlist[project_key]["instance_schema_var"]

        # ------------------------------------------------------------------ #
        # 4. Scan the project
        # ------------------------------------------------------------------ #
        
        #Adding A try-except ?? : --> TODO
        
        project_variables = project.get_variables()
        all_datasets      = project.list_datasets()
        all_recipes       = project.list_recipes()
        

        sf_datasets  = [d for d in all_datasets if d.get("type") == "Snowflake"]
        sql_recipes  = [r for r in all_recipes  if r.get("type") == "sql_query"]
        py_recipes   = [r for r in all_recipes  if r.get("type") == "python"]

      
        issues = []
        issues += detect_wrk_schemas_in_variables(project, project_variables, allowed_schemas)
        issues += detect_schemas_in_sql_recipes(project, sql_recipes, project_variables, allowed_schemas, instance_schema_var)
        issues += detect_schemas_in_python_recipes(project, py_recipes, project_variables, allowed_schemas, instance_schema_var)
        issues += detect_schemas_in_datasets(project, sf_datasets, project_variables, allowed_schemas, instance_schema_var)

        # ------------------------------------------------------------------ #
        # 5. Return result
        # ------------------------------------------------------------------ #
        details = {
            "project_key":         project_key,
            "allowed_schemas":     allowed_schemas,
            "instance_schema_var": instance_schema_var,
            "issue_count":         len(issues),
            "issues":              issues,
        }

        if not issues:
            return ProjectStandardsCheckRunResult.success(
                message=(
                    f"No unauthorized Snowflake schema usage detected in project "
                    f"'{project_key}'. Allowed: {', '.join(allowed_schemas)}."
                ),
                details=details,
            )

        # Build a message for the issues case
        by_type = {}
        for issue in issues:
            by_type.setdefault(issue["type"], []).append(issue["name"])

        summary_parts = [
            f"{len(names)} {itype}" for itype, names in by_type.items()
        ]
        summary = "; ".join(summary_parts)

        return ProjectStandardsCheckRunResult.failure(
            severity=severity,
            message=(
                f"Unauthorized Snowflake schema usage found in project '{project_key}': "
                f"{len(issues)} issue(s) — {summary}. "
                f"Allowed schemas: {', '.join(allowed_schemas)}."
            ),
            details=details,
        )