"""
architect/harness.py
--------------------
Architect Agent Harness subclassing the BaseAgentHarness.
Registers the full suite of integration tools for the Systems Architect role.
"""

from src.agents.utils.harness_base import BaseAgentHarness
from src.services import *

class ArchitectAgentHarness(BaseAgentHarness):
    """
    Initialises the execution sandbox and builds the tool-list for the
    Architect agent.  Does NOT create or invoke the agent itself — that
    responsibility belongs to index.py.
    """

    def __init__(self, sandbox_name: str = "architect-sandbox"):
        super().__init__(sandbox_name=sandbox_name)

        # 1. Instantiate service clients (best-effort — missing config is non-fatal)
        gh_client = self._try_init(GitHubAPIClient)
        gl_client = self._try_init(GitLabAPIClient)
        jr_client = self._try_init(JiraAPIClient)
        sl_client = slack_client  # pre-instantiated singleton from slack_service

        # 2. Build tool list
        if gh_client:
            github_tool_classes = [
                ListRepositoriesTool, GetRepositoryTool, ListIssuesTool, CreateIssueTool,
                UpdateIssueTool, ListPullRequestsTool, CreatePullRequestTool, MergePullRequestTool,
                CheckPRStatusTool, ListWorkflowRunsTool, TriggerWorkflowTool, CancelWorkflowRunTool,
                ListCommitsTool, CompareCommitsTool, ListBranchesTool, CreateBranchTool, ListDeploymentsTool,
                CreateDeploymentTool, GetFileContentTool, CreateOrUpdateFileTool, ListWebhooksTool,
                CreateWebhookTool, SearchRepositoriesTool, SearchIssuesTool, RequestReviewerTool,
                GetTreeTool,
            ]
            self._register_tools(github_tool_classes, github_client=gh_client)

        if gl_client:
            gitlab_tool_classes = [
                ListGitLabProjectsTool, GetGitLabFileContentTool, CreateOrUpdateGitLabFileTool,
                CreateGitLabBranchTool, CreateGitLabMergeRequestTool,
            ]
            self._register_tools(gitlab_tool_classes, gitlab_client=gl_client)

        if jr_client:
            jira_tool_classes = [
                JiraGetMyselfTool, JiraCreateIssueTool, JiraSearchIssuesTool,
                JiraGetIssueTool, JiraAddCommentTool, JiraTransitionIssueTool,
            ]
            self._register_tools(jira_tool_classes, jira_client=jr_client)

        if sl_client:
            slack_tool_classes = [
                SendMessageTool, CreateChannelTool, InviteUsersTool, ArchiveChannelTool,
                PinMessageTool, FindUserByEmailTool, FetchLatestMessageTool,
            ]
            self._register_tools(slack_tool_classes, slack_client=sl_client)