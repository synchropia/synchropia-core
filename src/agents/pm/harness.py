"""
pm/harness.py
-------------
Product Manager Agent Harness subclassing the BaseAgentHarness.
Registers tools for Jira requirements tracing and Slack updates.
"""

from src.agents.utils.harness_base import BaseAgentHarness
from src.services import (
    JiraAPIClient,
    JiraSearchIssuesTool,
    JiraCreateIssueTool,
    JiraGetIssueTool,
    JiraTransitionIssueTool,
    JiraAddCommentTool,
    slack_client,
    SendMessageTool,
    CreateChannelTool,
    InviteUsersTool,
)

class PMAgentHarness(BaseAgentHarness):
    """
    Initializes the PM Agent execution sandbox and registers the Jira and Slack tools.
    """

    def __init__(self, sandbox_name: str = "pm-sandbox"):
        super().__init__(sandbox_name=sandbox_name)

        # 1. Instantiate service clients
        jr_client = self._try_init(JiraAPIClient)
        sl_client = slack_client

        # 2. Register PM Tools
        if jr_client:
            jira_tool_classes = [
                JiraSearchIssuesTool,
                JiraCreateIssueTool,
                JiraGetIssueTool,
                JiraTransitionIssueTool,
                JiraAddCommentTool,
            ]
            self._register_tools(jira_tool_classes, jira_client=jr_client)

        if sl_client:
            slack_tool_classes = [
                SendMessageTool,
                CreateChannelTool,
                InviteUsersTool,
            ]
            self._register_tools(slack_tool_classes, slack_client=sl_client)
