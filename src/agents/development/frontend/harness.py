"""
development/frontend/harness.py
--------------------
Frontend Agent Harness subclassing the BaseAgentHarness.
Registers only tools relevant to front-end development, repositories, branches, and PRs.
"""

from src.agents.utils.harness_base import BaseAgentHarness
from src.services import *

class FrontendAgentHarness(BaseAgentHarness):
    """
    Initialises the execution sandbox and builds the tool-list for the
    Frontend agent. Focused on frontend client code changes.
    """

    def __init__(self, sandbox_name: str = "frontend-sandbox", org_id: str | None = None):
        super().__init__(sandbox_name=sandbox_name, org_id=org_id)

        # 1. Instantiate service clients
        gh_client = self._try_init(GitHubAPIClient)
        gl_client = self._build_gitlab_client()
        sl_client = slack_client

        # 2. Register Frontend tools
        if gh_client:
            github_tool_classes = [
                GetTreeTool, GetFileContentTool, CreateOrUpdateFileTool,
                CreateBranchTool, ListBranchesTool, CreatePullRequestTool, CheckPRStatusTool,
            ]
            self._register_tools(github_tool_classes, github_client=gh_client)

        if gl_client:
            gitlab_tool_classes = [
                ListGitLabProjectsTool, GetGitLabFileContentTool, CreateOrUpdateGitLabFileTool,
                CreateGitLabBranchTool, CreateGitLabMergeRequestTool,
            ]
            self._register_tools(gitlab_tool_classes, gitlab_client=gl_client)

        if sl_client:
            slack_tool_classes = [
                SendMessageTool,
            ]
            self._register_tools(slack_tool_classes, slack_client=sl_client)
