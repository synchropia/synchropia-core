"""
development/qa/harness.py
--------------------
QA Agent Harness subclassing the BaseAgentHarness.
Registers GitHub workflow checks, Jira logs (including issue creation),
Slack updates, and the full SonarQube code-quality inspection suite.
"""

from src.agents.utils.harness_base import BaseAgentHarness
from src.services import (
    GitHubAPIClient,
    ListWorkflowRunsTool,
    GetFileContentTool,
    JiraAPIClient,
    JiraCreateIssueTool,       # ← NEW: QA can create remediation tickets
    JiraSearchIssuesTool,      # ← NEW: QA can search existing tickets
    JiraAddCommentTool,
    JiraGetIssueTool,
    JiraTransitionIssueTool,
    slack_client,
    SendMessageTool,
    sonarqube_client,
    SonarQubeGetProjectStatusTool,
    SonarQubeGetProjectMeasuresTool,
    SonarQubeGetMetricHistoryTool,   # ← NEW: trend analysis
    SonarQubeListIssesTool,
    SonarQubeSearchProjectsTool,
    SonarQubeListGithubReposTool,
)


class QAAgentHarness(BaseAgentHarness):
    """
    Initializes the QA Agent execution sandbox and registers:
    - GitHub workflow auditing tools
    - Jira tools including **JiraCreateIssueTool** for raising fix tickets
    - Slack notification tool
    - Full SonarQube code-quality inspection suite (status, measures, history,
      issues, project search, GitHub repo listing)
    - **LSP semantic tools** (inherited from BaseAgentHarness via LSP_TOOLS):
      lsp_diagnostics, lsp_definition, lsp_references, lsp_hover,
      lsp_workspace_symbols, lsp_code_actions, lsp_format_document.
      These provide fast pre-PR inner-loop checks BEFORE SonarQube's post-CI gate.
    """

    def __init__(self, sandbox_name: str = "qa-sandbox"):
        super().__init__(sandbox_name=sandbox_name)

        # 1. Instantiate service clients
        gh_client = self._try_init(GitHubAPIClient)
        jr_client = self._try_init(JiraAPIClient)
        sl_client = slack_client
        sq_client = sonarqube_client  # module-level singleton

        # 2. Register GitHub Tools
        if gh_client:
            self._register_tools(
                [ListWorkflowRunsTool, GetFileContentTool],
                github_client=gh_client,
            )

        # 3. Register Jira Tools
        #    JiraCreateIssueTool is critical: QA needs to create fix tasks for Backend.
        if jr_client:
            self._register_tools(
                [
                    JiraCreateIssueTool,
                    JiraSearchIssuesTool,
                    JiraAddCommentTool,
                    JiraGetIssueTool,
                    JiraTransitionIssueTool,
                ],
                jira_client=jr_client,
            )

        # 4. Register Slack Tools
        if sl_client:
            self._register_tools([SendMessageTool], slack_client=sl_client)

        # 5. Register SonarQube Tools (full suite)
        if sq_client:
            self._register_tools(
                [
                    SonarQubeGetProjectStatusTool,
                    SonarQubeGetProjectMeasuresTool,
                    SonarQubeGetMetricHistoryTool,
                    SonarQubeListIssesTool,
                    SonarQubeSearchProjectsTool,
                    SonarQubeListGithubReposTool,
                ],
                sonarqube_client=sq_client,
            )
