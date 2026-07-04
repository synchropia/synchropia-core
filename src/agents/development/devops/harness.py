"""
development/devops/harness.py
--------------------
DevOps Agent Harness subclassing the BaseAgentHarness.
Registers DevOps-specific GitHub/GitLab APIs and the read_image tool.
"""

from src.agents.utils.harness_base import BaseAgentHarness
from src.services import *
from .tools import read_image

class DevOpsAgentHarness(BaseAgentHarness):
    """
    Initializes the DevOps Agent execution sandbox and registers the deployment and read_image tools.
    """

    def __init__(self, sandbox_name: str = "devops-sandbox"):
        super().__init__(sandbox_name=sandbox_name)

        # 1. Instantiate service clients
        gh_client = self._try_init(GitHubAPIClient)
        gl_client = self._try_init(GitLabAPIClient)
        sl_client = slack_client

        # 2. Register DevOps GitHub/GitLab tools
        if gh_client:
            github_tool_classes = [
                ListWorkflowRunsTool, TriggerWorkflowTool, CancelWorkflowRunTool,
                ListDeploymentsTool, CreateDeploymentTool, ListWebhooksTool,
                CreateWebhookTool, GetFileContentTool, CreateOrUpdateFileTool,
            ]
            self._register_tools(github_tool_classes, github_client=gh_client)

        if gl_client:
            gitlab_tool_classes = [
                ListGitLabProjectsTool, GetGitLabFileContentTool,
                CreateOrUpdateGitLabFileTool, CreateGitLabBranchTool,
            ]
            self._register_tools(gitlab_tool_classes, gitlab_client=gl_client)

        if sl_client:
            slack_tool_classes = [
                SendMessageTool,
            ]
            self._register_tools(slack_tool_classes, slack_client=sl_client)

        # 3. Register custom read_image tool
        self.tools.append(read_image)
