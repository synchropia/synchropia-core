"""
data_engineering/data_engineer/harness.py
--------------------
Data Engineer Agent Harness subclassing the BaseAgentHarness.
Registers Slack notifications for ETL pipeline status.
"""

from src.agents.utils.harness_base import BaseAgentHarness
from src.services import (
    slack_client,
    SendMessageTool,
)

class DataEngineerAgentHarness(BaseAgentHarness):
    """
    Initializes the Data Engineer Agent execution sandbox and registers the Slack notification tool.
    """

    def __init__(self, sandbox_name: str = "data-engineer-sandbox"):
        super().__init__(sandbox_name=sandbox_name)

        # Register Slack messaging
        sl_client = slack_client
        if sl_client:
            slack_tool_classes = [
                SendMessageTool,
            ]
            self._register_tools(slack_tool_classes, slack_client=sl_client)
