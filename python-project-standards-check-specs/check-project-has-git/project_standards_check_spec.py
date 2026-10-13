from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    The superclass is setting those fields:
    self.config: the dict of the configuration of the object
    self.plugin_config: the dict of the plugin settings
    self.project: the current `DSSProject` to use in your check spec
    """
    def run(self):
        """
        Run the check

        :returns: the run result.
            Use `ProjectStandardsCheckRunResult.success(message)` or `ProjectStandardsCheckRunResult.failure(severity, message)` depending on the result.
            Use `ProjectStandardsCheckRunResult.not_applicable(message)` if the check is not applicable to the project.
            Use `ProjectStandardsCheckRunResult.error(message)` if you want to mark the check as an error. You can also raise an Exception.
        """
        project = self.project
        severity = int(self.config["severity"])

        try:
            # Access Git configuration
            git = project.get_project_git()
            status = git.get_status()

            remotes = status.get("remotes", [])
            current_branch = status.get("currentBranch", None)

            has_remote = len(remotes) > 0
            remote_url = remotes[0] if has_remote else None

            details = {
                "has_remote": has_remote,
                "remotes": remotes,
                "current_branch": current_branch,
                "remote_url": remote_url,
            }

            # ---- Decision ----
            if has_remote and remote_url:
                return ProjectStandardsCheckRunResult.success(
                    message=f"Project is connected to a Git repository (remote: {remote_url})",
                    details=details,
                )

            return ProjectStandardsCheckRunResult.failure(
                severity=severity,
                message="Project is not connected to any Git remote repository.",
                details=details,
            )

        except Exception as e:
            # Git feature not enabled or permissions problem
            return ProjectStandardsCheckRunResult.error(
                message=f"Error checking Git configuration: {str(e)}"
            )