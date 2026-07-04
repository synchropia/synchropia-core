"""
development/qa/harness.py
--------------------
QA Agent Harness subclassing the BaseAgentHarness.
Registers GitHub workflow checks, Jira logs, and Slack updates.
"""

from src.agents.utils.harness_base import BaseAgentHarness
from src.services import (
    GitHubAPIClient,
    ListWorkflowRunsTool,
    GetFileContentTool,
    JiraAPIClient,
    JiraAddCommentTool,
    JiraGetIssueTool,
    JiraTransitionIssueTool,
    slack_client,
    SendMessageTool,
)

class QAAgentHarness(BaseAgentHarness):
    """
    Initializes the QA Agent execution sandbox and registers workflow auditing and Jira commenting tools.
    """

    def __init__(self, sandbox_name: str = "qa-sandbox"):
        super().__init__(sandbox_name=sandbox_name)

        # 1. Instantiate service clients
        gh_client = self._try_init(GitHubAPIClient)
        jr_client = self._try_init(JiraAPIClient)
        sl_client = slack_client

        # 2. Register QA Tools
        if gh_client:
            github_tool_classes = [
                ListWorkflowRunsTool,
                GetFileContentTool,
            ]
            self._register_tools(github_tool_classes, github_client=gh_client)

        if jr_client:
            jira_tool_classes = [
                JiraAddCommentTool,
                JiraGetIssueTool,
                JiraTransitionIssueTool,
            ]
            self._register_tools(jira_tool_classes, jira_client=jr_client)

        if sl_client:
            slack_tool_classes = [
                SendMessageTool,
            ]
            self._register_tools(slack_tool_classes, slack_client=sl_client)
