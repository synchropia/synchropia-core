from typing import Dict, Any, Optional, List, Type
import aiohttp,sys
from loguru import logger 
import httpx,requests
import time
import jwt
from pydantic import BaseModel, Field
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
import os 
from dotenv import load_dotenv 
from src.config.settings import settings


load_dotenv()


from abc import ABC, abstractmethod
from langchain_core.tools import BaseTool
from langchain_core.tools.base import ArgsSchema

from src.utils.logger import logger 

# =====================================================================
# GitHub App Permission Scope Definitions
# ---------------------------------------------------------------------
# These are the official GitHub App permissions organized by category.
# GitHub Apps use granular permissions (not OAuth scopes). Each
# permission is a distinct access right granted to the GitHub App
# installation. The system pre-selects these when creating/requesting
# a GitHub App installation.
# =====================================================================

class GitHubAppPermission:
    """GitHub App permission constants.

    Ref: https://docs.github.com/en/rest/overview/permissions-required-for-github-apps
    """

    # ── Read + Write Permissions ────────────────────────────────────
    ACTIONS                                    = "actions"
    CHECKS                                     = "checks"
    CODE_QUALITY                               = "code_quality"
    COMMIT_STATUSES                            = "commit_statuses"
    CONTENTS                                   = "contents"
    DEPLOYMENTS                                = "deployments"
    ISSUES                                     = "issues"
    MERGE_QUEUES                               = "merge_queues"
    PULL_REQUESTS                              = "pull_requests"
    SECRET_SCANNING_ALERT_DISMISSAL_REQUESTS   = "secret_scanning_alert_dismissal_requests"
    SECRET_SCANNING_ALERTS                     = "secret_scanning_alerts"
    SECRET_SCANNING_PUSH_PROTECTION_BYPASS_REQUESTS = "secret_scanning_push_protection_bypass_requests"
    SECRETS                                    = "secrets"
    VARIABLES                                  = "variables"
    WEBHOOKS                                   = "webhooks"
    WORKFLOWS                                  = "workflows"

    # ── Read-Only Permissions (mandatory) ──────────────────────────
    METADATA                                   = "metadata"


# ── Permission Category Registry ──────────────────────────────────

GITHUB_APP_PERMISSION_CATEGORIES: dict[str, dict[str, str]] = {
    "Read and Write Access": {
        "Actions": "read_and_write",
        "Checks": "read_and_write",
        "Code quality": "read_and_write",
        "Commit statuses": "read_and_write",
        "Contents": "read_and_write",
        "Deployments": "read_and_write",
        "Issues": "read_and_write",
        "Merge queues": "read_and_write",
        "Pull requests": "read_and_write",
        "Secret scanning alert dismissal requests": "read_and_write",
        "Secret scanning alerts": "read_and_write",
        "Secret scanning push protection bypass requests": "read_and_write",
        "Secrets": "read_and_write",
        "Variables": "read_and_write",
        "Webhooks": "read_and_write",
        "Workflows": "read_and_write",
    },
    "Read-Only Access (mandatory)": {
        "Metadata": "read_only",
    },
}


# ── Tool → Required Permission Mapping ────────────────────────────

GITHUB_TOOL_PERMISSIONS: dict[str, list[str]] = {
    "list_repositories":              [GitHubAppPermission.CONTENTS, GitHubAppPermission.METADATA],
    "get_repository":                 [GitHubAppPermission.CONTENTS, GitHubAppPermission.METADATA],
    "list_issues":                    [GitHubAppPermission.ISSUES, GitHubAppPermission.METADATA],
    "create_issue":                   [GitHubAppPermission.ISSUES],
    "update_issue":                   [GitHubAppPermission.ISSUES],
    "list_pull_requests":             [GitHubAppPermission.PULL_REQUESTS, GitHubAppPermission.METADATA],
    "create_pull_request":            [GitHubAppPermission.PULL_REQUESTS, GitHubAppPermission.CONTENTS],
    "merge_pull_request":             [GitHubAppPermission.PULL_REQUESTS, GitHubAppPermission.CONTENTS],
    "check_pr_status":                [GitHubAppPermission.PULL_REQUESTS],
    "list_workflow_runs":             [GitHubAppPermission.ACTIONS, GitHubAppPermission.METADATA],
    "trigger_workflow":               [GitHubAppPermission.ACTIONS, GitHubAppPermission.WORKFLOWS, GitHubAppPermission.CONTENTS],
    "cancel_workflow_run":            [GitHubAppPermission.ACTIONS],
    "list_commits":                   [GitHubAppPermission.CONTENTS, GitHubAppPermission.METADATA],
    "compare_commits":                [GitHubAppPermission.CONTENTS],
    "list_branches":                  [GitHubAppPermission.CONTENTS, GitHubAppPermission.METADATA],
    "create_branch":                  [GitHubAppPermission.CONTENTS],
    "list_deployments":               [GitHubAppPermission.DEPLOYMENTS, GitHubAppPermission.METADATA],
    "create_deployment":              [GitHubAppPermission.DEPLOYMENTS, GitHubAppPermission.CONTENTS],
    "get_file_content":               [GitHubAppPermission.CONTENTS],
    "create_or_update_file":          [GitHubAppPermission.CONTENTS],
    "list_webhooks":                  [GitHubAppPermission.WEBHOOKS, GitHubAppPermission.METADATA],
    "create_webhook":                 [GitHubAppPermission.WEBHOOKS],
    "search_repositories":            [GitHubAppPermission.METADATA],
    "search_issues":                  [GitHubAppPermission.ISSUES, GitHubAppPermission.METADATA],
    "request_reviewer":               [GitHubAppPermission.PULL_REQUESTS],
    "get_tree":                       [GitHubAppPermission.CONTENTS],
}


def describe_github_app_permissions() -> str:
    """Returns a human-readable summary of all GitHub App permissions."""
    lines = ["GitHub App — Required Permissions", "=" * 50]
    for category, resources in GITHUB_APP_PERMISSION_CATEGORIES.items():
        lines.append(f"\n{category}:")
        for resource, access in resources.items():
            lines.append(f"  {resource}: {access}")
    return "\n".join(lines)


def permissions_for_tool(tool_name: str) -> list[str]:
    """Return the list of GitHub App permissions required for a given tool."""
    return GITHUB_TOOL_PERMISSIONS.get(tool_name, [])


class ToolInputSchema(BaseModel):
    """Define your input schema in subclasses via args_schema override."""

class AbstractTool(BaseTool, ABC):
    name: str
    description: str
    args_schema: Optional[Type[BaseModel]] = None
    return_direct: bool = False

    @abstractmethod
    def _run(
        self, *args, run_manager: Optional[CallbackManagerForToolRun] = None, **kwargs
    ):
        """Sync execution logic to implement."""

    @abstractmethod
    async def _arun(
        self,
        *args,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
        **kwargs,
    ):
        """Async execution logic to implement."""

# Global token cache to prevent duplicate JWT exchange requests and speed up execution
_GITHUB_ACCESS_TOKEN_CACHE: dict[int, tuple[str, int]] = {}

class GitHubAPIClient:
    """
    A comprehensive client for interacting with the GitHub REST API.

    This client provides both synchronous and asynchronous methods for making
    HTTP requests to GitHub's REST API v3. It handles authentication, request
    formatting, response parsing, and error handling.

    Features:
        - app_id , private_key_path and installation_id authentication
        - Support for both sync and async operations
        - Automatic JSON request/response handling
        - Comprehensive error handling and logging
        - Rate limiting awareness
        - Custom base URL support (for GitHub Enterprise)

    Prerequisites:
        - Valid GitHub App ID, private key path and installation ID
        - Network connectivity to GitHub API
        - Required permissions for the operations being performed

    Authentication:
        Uses Personal Access Token authentication. The token should have
        the necessary scopes for the operations you want to perform:
        - repo: Full access to repositories
        - workflow: Access to GitHub Actions
        - admin:org: Organization administration
        - user: User profile and email access

    Rate Limiting:
        GitHub API has rate limits. This client respects these limits
        and will return appropriate error messages when limits are exceeded.

    Example Usage:
        ```python
        # Initialize with token
        client = GitHubAPIClient(app_id=,private_key_path=,installation_id=,base_url="https://api.github.com")

        # Make a GET request
        repos = client.make_request("GET", "user/repos")

        # Make a POST request with data
        new_issue = client.make_request("POST", "repos/owner/repo/issues", {
            "title": "Bug report",
            "body": "Description of the bug"
        })

        # Async request
        async def get_user():
            return await client.make_async_request("GET", "user")
        ```
    """

    def __init__(self, app_id: str = None, private_key_path: str = None, installation_id: str = None, base_url: str = "https://api.github.com"):
        """
        Initialize the GitHub API client.

        Args:
            app_id (str) : GitHub App ID
            private_key_path (str) : Path to the GitHub App private key
            installation_id (str) : GitHub Installation ID
            base_url (str): Base URL for the GitHub API.
                           Defaults to "https://api.github.com" for GitHub.com.
                           Use custom URL for GitHub Enterprise instances.

        Raises:
            ValueError: If token is empty or invalid.

        Example:
            ```python
            # GitHub.com
            client = GitHubAPIClient(app_id=,private_key_path=,installation_id=,base_url="https://api.github.com")

            # GitHub Enterprise
            client = GitHubAPIClient(app_id=,private_key_path=,installation_id=,base_url="https://github.yourcompany.com/api/v3")
            ```
        """
        self.app_id = app_id or settings.GITHUB_APP_ID
        self.private_key_path = private_key_path or settings.GITHUB_APP_PRIVATE_KEY_PATH
        if self.private_key_path:
            self.private_key_path = self.private_key_path.strip('"\'')
        self.installation_id = installation_id or settings.GITHUB_INSTALLATION_ID
        self.base_url = base_url.rstrip("/")
        
        token = self._get_installation_access_token()
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "DevOps-Agent/1.0",
        }
        self.sync_client = httpx.Client(headers=self.headers, timeout=30.0)
        self.async_client = httpx.AsyncClient(headers=self.headers, timeout=30.0)

    def __del__(self):
        try:
            self.sync_client.close()
        except Exception:
            pass

    def _get_installation_access_token(self) -> str:
        """
        Generate a JWT signed with the GitHub App's private key and exchange it
        for an installation access token.
        """
        if not self.app_id:
            raise ValueError("app_id is not set. Cannot authenticate as GitHub App.")
        if not self.private_key_path:
            raise ValueError("private_key_path is not set. Cannot authenticate as GitHub App.")
        if not self.installation_id:
            raise ValueError("installation_id is not set. Cannot authenticate as GitHub App.")

        # Check in-memory global cache
        global _GITHUB_ACCESS_TOKEN_CACHE
        now = int(time.time())
        inst_id = int(self.installation_id)
        if inst_id in _GITHUB_ACCESS_TOKEN_CACHE:
            token, expiry = _GITHUB_ACCESS_TOKEN_CACHE[inst_id]
            if expiry > now + 60:
                logger.debug(f"Reusing cached GitHub access token for installation {inst_id}")
                return token

        try:
            with open(self.private_key_path, "r", encoding="utf-8") as f:
                private_key = f.read()
        except Exception as e:
            logger.error(f"Failed to read private key at {self.private_key_path}: {e}")
            raise ValueError(f"Failed to read private key: {e}")

        payload = {
            "iat": now - 60,
            "exp": now + 600,
            "iss": self.app_id,
        }

        try:
            encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")
        except Exception as e:
            logger.error(f"Failed to encode JWT: {e}")
            raise ValueError(f"Failed to encode JWT: {e}")

        if isinstance(encoded_jwt, bytes):
            encoded_jwt = encoded_jwt.decode("utf-8")

        url = f"{self.base_url}/app/installations/{self.installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {encoded_jwt}",
            "Accept": "application/vnd.github.v3+json",
        }

        try:
            response = requests.post(url, headers=headers)
            response.raise_for_status()
            token_data = response.json()
            token = token_data["token"]
            
            # Cache the token (typically valid for 1 hour, expire 2 minutes early)
            expiry_secs = 3480
            _GITHUB_ACCESS_TOKEN_CACHE[inst_id] = (token, now + expiry_secs)
            logger.success(f"Successfully requested and cached new GitHub access token for installation {inst_id}")
            return token
        except Exception as e:
            logger.error(f"Failed to retrieve installation access token: {e}")
            raise ValueError(f"Failed to retrieve installation access token: {e}")

    def make_request(
        self, method: str, endpoint: str, data: Dict[Any, Any] = None
    ) -> Dict[Any, Any]:
        """
        Make a synchronous HTTP request to the GitHub API.

        This method handles the complete request lifecycle including authentication,
        request formatting, response parsing, and error handling.

        Args:
            method (str): HTTP method (GET, POST, PUT, DELETE, PATCH).
            endpoint (str): API endpoint path (e.g., "user/repos", "repos/owner/repo/issues").
                          Should not include the base URL or leading slash.
            data (Dict[Any, Any], optional): Request payload for POST/PUT/PATCH requests.
                                           Will be automatically converted to JSON.

        Returns:
            Dict[Any, Any]: Parsed JSON response from the GitHub API.
                           For successful requests, contains the API response data.
                           For errors, contains error details.

        Raises:
            httpx.HTTPStatusError: For HTTP error codes.
            httpx.RequestError: For network or connection errors.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        logger.info(f"Making {method} request to: {url} (reusing connection pool)")
        if data:
            logger.debug(f"Request data payload: {data}")

        try:
            if method.upper() in ["POST", "PUT", "PATCH"] and data:
                response = self.sync_client.request(
                    method, url, json=data
                )
            else:
                response = self.sync_client.request(method, url)

            logger.info(f"Response status code: {response.status_code}")
            response.raise_for_status()
            
            try:
                res_json = response.json()
                logger.success(f"Successfully processed {method} request to {endpoint}")
                return res_json
            except ValueError:
                logger.success(f"Successfully processed {method} request to {endpoint} (no JSON body)")
                return {"status": response.status_code, "message": "Success"}

        except httpx.HTTPStatusError as e:
            logger.error(f"Request to {url} failed: {str(e)}")
            return {"error": f"Request failed: {str(e)}"}
        except httpx.RequestError as e:
            logger.error(f"Network request to {url} failed: {str(e)}")
            return {"error": f"Network request failed: {str(e)}"}

    async def make_async_request(
        self, method: str, endpoint: str, data: Dict[Any, Any] = None
    ) -> Dict[Any, Any]:
        """
        Make an asynchronous HTTP request to the GitHub API.

        This method provides the same functionality as make_request but operates
        asynchronously, allowing for non-blocking API calls in async contexts.

        Args:
            method (str): HTTP method (GET, POST, PUT, DELETE, PATCH).
            endpoint (str): API endpoint path (e.g., "user/repos", "repos/owner/repo/issues").
                          Should not include the base URL or leading slash.
            data (Dict[Any, Any], optional): Request payload for POST/PUT/PATCH requests.
                                           Will be automatically converted to JSON.

        Returns:
            Dict[Any, Any]: Parsed JSON response from the GitHub API.
                           For successful requests, contains the API response data.
                           For errors, contains error details.

        Raises:
            httpx.HTTPStatusError: For HTTP error codes.
            httpx.RequestError: For network or connection errors.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        logger.info(f"Making async {method} request to: {url} (reusing connection pool)")
        if data:
            logger.debug(f"Async request data payload: {data}")

        try:
            if method.upper() in ["POST", "PUT", "PATCH"] and data:
                response = await self.async_client.request(
                    method, url, json=data
                )
            else:
                response = await self.async_client.request(method, url)

            logger.info(f"Async response status code: {response.status_code}")
            response.raise_for_status()
            
            try:
                res_json = response.json()
                logger.success(f"Successfully processed async {method} request to {endpoint}")
                return res_json
            except ValueError:
                logger.success(f"Successfully processed async {method} request to {endpoint} (no JSON body)")
                return {"status": response.status_code, "message": "Success"}

        except httpx.HTTPStatusError as e:
            logger.error(f"Async request to {url} failed: {str(e)}")
            return {"error": f"Async request failed: {str(e)}"}
        except httpx.RequestError as e:
            logger.error(f"Async network request to {url} failed: {str(e)}")
            return {"error": f"Async network request failed: {str(e)}"}
        except Exception as e:
            logger.error(f"Async request to {url} failed with error: {str(e)}")
            return {"error": f"Unexpected error: {str(e)}"}


def _is_dependency_file(path: str) -> bool:
    """Return True if the path is a dependency manifest or lock file.

    Covers ``*.lock`` lockfiles (uv.lock, poetry.lock, Cargo.lock, yarn.lock),
    JS/Python ecosystem lockfiles that do not use a ``.lock`` extension, and the
    dependency manifests that pair with them (package.json, pyproject.toml,
    requirements.txt, Cargo.toml, go.mod, ...).
    """
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith(".lock"):
        return True
    if name.startswith("requirements") and name.endswith(".txt"):
        return True
    return name in {
        # lockfiles without a trailing .lock extension
        "package-lock.json",
        "pnpm-lock.yaml",
        "pnpm-lock.yml",
        "bun.lockb",
        "deno.lock",
        "conda-lock.yml",
        # dependency manifests
        "package.json",
        "pyproject.toml",
        "pipfile",
        "setup.py",
        "setup.cfg",
        "cargo.toml",
        "go.mod",
        "go.sum",
        "gemfile",
        "composer.json",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "csproj",
    }


def _show_structure(obj: Any, depth: int = 0, max_depth: int = 5) -> None:
    """Recursively print the shape (keys + types) of a GitHub API response."""
    pad = "  " * depth
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                print(f"{pad}{k}: dict")
                _show_structure(v, depth + 1, max_depth)
            elif isinstance(v, list):
                print(f"{pad}{k}: list[{len(v)}]")
                if v and depth < max_depth:
                    _show_structure(v[0], depth + 1, max_depth)
            else:
                print(f"{pad}{k}: {type(v).__name__} = {str(v)[:60]!r}")
    elif isinstance(obj, list):
        if not obj:
            print(f"{pad}(empty list)")
        elif depth < max_depth:
            _show_structure(obj[0], depth, max_depth)
    else:
        print(f"{pad}{type(obj).__name__} = {str(obj)[:60]!r}")


# ============= REPOSITORY MANAGEMENT TOOLS =============
class ListRepositoriesInput(BaseModel):
    owner: str = Field(description="GitHub username or organization name")
    repo_type: str = Field(
        default="all",
        description="Type of repos: 'all', 'owner', 'public', 'private', 'member'",
    )
    per_page: int = Field(
        default=30, description="Number of repositories per page (max 100)"
    )

class ListRepositoriesOutput(BaseModel):
    id: int 
    name: str 
    full_name: str 
    description: Optional[str] = None 
    private: bool 
    visibility: str 
    language: Optional[str] = None 
    default_branch: str 
    html_url: str 
    clone_url: str 
    created_at: str 
    updated_at: str 
    pushed_at: str 
    size: int 
    topics: list 
    archived: bool 
    has_pages: bool 
    owner: str 
    

class ListRepositoriesTool(AbstractTool):
    """
    A LangChain tool for listing GitHub repositories for a user or organization.

    This tool provides comprehensive repository listing functionality with various
    filtering options and pagination support. It can list repositories for both
    individual users and organizations.

    Features:
        - List repositories for users or organizations
        - Filter by repository type (all, owner, public, private, member)
        - Pagination support with configurable page size
        - Automatic sorting by last updated
        - Support for both sync and async operations

    Repository Types:
        - all: All repositories the user has access to
        - owner: Repositories owned by the user/organization
        - public: Public repositories
        - private: Private repositories (requires appropriate permissions)
        - member: Repositories where the user is a member

    Prerequisites:
        - Valid GitHub Personal Access Token
        - Appropriate permissions for the repository types being accessed
        - Access to the specified user/organization

    Example Usage:
        ```python
        # List all repositories for a user
        tool = ListRepositoriesTool(github_client=client)
        result = tool.run({"owner": "octocat"})

        # List only public repositories
        result = tool.run({
            "owner": "microsoft",
            "repo_type": "public",
            "per_page": 50
        })

        # List private repositories (requires appropriate permissions)
        result = tool.run({
            "owner": "myorg",
            "repo_type": "private"
        })
        ```

    Output Format:
        Returns a list of repository objects containing:
        - Repository name and full name
        - Description and homepage URL
        - Visibility (public/private)
        - Language and topics
        - Star and fork counts
        - Last updated timestamp
        - Default branch information

    Error Handling:
        - Authentication errors (401)
        - Permission errors (403)
        - Not found errors (404)
        - Rate limiting errors (429)
        - Network connectivity issues
    """

    name: str = "list_repositories"
    description: str = "List repositories for a user or organization"
    args_schema: Type[BaseModel] = ListRepositoriesInput
    github_client: GitHubAPIClient  # <--- DECLARE THE FIELD

    def _run(
        self,
        owner: str,
        repo_type: str = "all",
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> List[ListRepositoriesOutput]:
        """
        Synchronously list repositories for a GitHub user or organization.

        Args:
            owner (str): GitHub username or organization name.
                       Examples: "octocat", "microsoft", "myorg"
            repo_type (str): Type of repositories to list.
                           Options: "all", "owner", "public", "private", "member"
            per_page (int): Number of repositories per page (1-100).
                          Default: 30, Maximum: 100
            run_manager (Optional[CallbackManagerForToolRun]): LangChain callback manager.

        Returns:
            Dict[Any, Any]: JSON response containing repository information.
                           Includes repository list and pagination metadata.

        Raises:
            Exception: For API errors, authentication issues, or network problems.
        """
        endpoint = f"orgs/{owner}/repos"
        params = {"type": repo_type, "per_page": per_page, "sort": "updated"}
        response = self.github_client.make_request("GET", endpoint, params)
        
        if "error" in response and response.get("status_code") == 404:
            endpoint = f"users/{owner}/repos"
        response = self.github_client.make_request("GET", endpoint, params)
        return [ListRepositoriesOutput(
            id=repo["id"],
            name=repo["name"],
            full_name=repo["full_name"],
            description=repo["description"],
            private=repo["private"],
            visibility=repo["visibility"],
            language=repo["language"],
            default_branch=repo["default_branch"],
            html_url=repo["html_url"],
            clone_url=repo["clone_url"],
            created_at=repo["created_at"],
            updated_at=repo["updated_at"],
            pushed_at=repo["pushed_at"],
            size=repo["size"],
            topics=repo["topics"],
            archived=repo["archived"],
            has_pages=repo["has_pages"],
            owner=repo["owner"]["login"]
        ) for repo in response]
        

    async def _arun(
        self,
        owner: str,
        repo_type: str = "all",
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> List[ListRepositoriesOutput]:
        """
        Asynchronously list repositories for a GitHub user or organization.

        Args:
            owner (str): GitHub username or organization name.
                       Examples: "octocat", "microsoft", "myorg"
            repo_type (str): Type of repositories to list.
                           Options: "all", "owner", "public", "private", "member"
            per_page (int): Number of repositories per page (1-100).
                          Default: 30, Maximum: 100
            run_manager (Optional[AsyncCallbackManagerForToolRun]): LangChain async callback manager.

        Returns:
            Dict[Any, Any]: JSON response containing repository information.
                           Same format as synchronous version.

        Raises:
            Exception: For API errors, authentication issues, or network problems.
        """
        endpoint = f"orgs/{owner}/repos"
        params = {"type": repo_type, "per_page": per_page, "sort": "updated"}
        return await self.github_client.make_async_request("GET", endpoint, params)


class GetRepositoryInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")


class GetRepositoryTool(AbstractTool):
    """
    A LangChain tool for retrieving detailed information about a specific GitHub repository.

    This tool provides comprehensive repository information including metadata,
    statistics, configuration, and status details. It's useful for getting
    complete repository profiles and checking repository health.

    Features:
        - Get detailed repository information
        - Access repository statistics and metrics
        - Retrieve repository configuration and settings
        - Check repository status and health
        - Support for both sync and async operations

    Information Retrieved:
        - Basic repository details (name, description, homepage)
        - Repository statistics (stars, forks, watchers, issues)
        - Configuration (default branch, topics, language)
        - Status information (archived, disabled, private/public)
        - Network information (parent, source, template)
        - Security settings (vulnerability alerts, security policy)

    Prerequisites:
        - Valid GitHub Personal Access Token
        - Access to the specified repository
        - Appropriate permissions for private repositories

    Example Usage:
        ```python
        # Get repository information
        tool = GetRepositoryTool(github_client=client)
        result = tool.run({
            "owner": "microsoft",
            "repo": "vscode"
        })

        # Get private repository (requires access)
        result = tool.run({
            "owner": "myorg",
            "repo": "private-repo"
        })
        ```

    Output Format:
        Returns a detailed repository object containing:
        - Repository metadata (id, name, full_name, description)
        - Statistics (stargazers_count, forks_count, open_issues_count)
        - Configuration (default_branch, topics, language, license)
        - Status flags (archived, disabled, private, fork)
        - URLs (html_url, clone_url, ssh_url)
        - Timestamps (created_at, updated_at, pushed_at)

    Error Handling:
        - Repository not found (404)
        - Access denied (403)
        - Authentication errors (401)
        - Network connectivity issues
    """

    name: str = "get_repository"
    description: str = "Get detailed information about a specific repository"
    args_schema: Type[BaseModel] = GetRepositoryInput
    github_client: GitHubAPIClient  # <--- DECLARE THE FIELD

    def _run(
        self,
        owner: str,
        repo: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        """
        Synchronously get detailed information about a GitHub repository.

        Args:
            owner (str): Repository owner username or organization name.
                       Examples: "microsoft", "octocat", "myorg"
            repo (str): Repository name.
                      Examples: "vscode", "Hello-World", "my-project"
            run_manager (Optional[CallbackManagerForToolRun]): LangChain callback manager.

        Returns:
            Dict[Any, Any]: JSON response containing detailed repository information.
                           Includes all repository metadata, statistics, and configuration.

        Raises:
            Exception: For API errors, authentication issues, or network problems.
        """
        endpoint = f"repos/{owner}/{repo}"
        return self.github_client.make_request("GET", endpoint)

    async def _arun(
        self,
        owner: str,
        repo: str,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        """
        Asynchronously get detailed information about a GitHub repository.

        Args:
            owner (str): Repository owner username or organization name.
                       Examples: "microsoft", "octocat", "myorg"
            repo (str): Repository name.
                      Examples: "vscode", "Hello-World", "my-project"
            run_manager (Optional[AsyncCallbackManagerForToolRun]): LangChain async callback manager.

        Returns:
            Dict[Any, Any]: JSON response containing detailed repository information.
                           Same format as synchronous version.

        Raises:
            Exception: For API errors, authentication issues, or network problems.
        """
        endpoint = f"repos/{owner}/{repo}"
        return await self.github_client.make_async_request("GET", endpoint)


# ============= ISSUES MANAGEMENT TOOLS =============
class ListIssuesInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    state: str = Field(
        default="open", description="Issue state: 'open', 'closed', 'all'"
    )
    labels: str = Field(default="", description="Comma-separated list of label names")
    assignee: str = Field(default="", description="Username of assignee")
    per_page: int = Field(default=30, description="Number of issues per page")


class ListIssuesTool(AbstractTool):
    """
    A LangChain tool for listing and filtering issues in a GitHub repository.

    This tool provides comprehensive issue listing functionality with multiple
    filtering options, making it easy to find specific issues based on various
    criteria such as state, labels, assignees, and more.

    Features:
        - List issues with multiple filtering options
        - Filter by issue state (open, closed, all)
        - Filter by labels (comma-separated)
        - Filter by assignee
        - Pagination support with configurable page size
        - Automatic sorting by last updated
        - Support for both sync and async operations

    Issue States:
        - open: Only open issues (default)
        - closed: Only closed issues
        - all: Both open and closed issues

    Filtering Options:
        - Labels: Comma-separated list of label names
        - Assignee: Specific GitHub username
        - Per page: Number of issues to return (1-100)

    Prerequisites:
        - Valid GitHub Personal Access Token
        - Access to the specified repository
        - Appropriate permissions for the repository

    Example Usage:
        ```python
        # List open issues
        tool = ListIssuesTool(github_client=client)
        result = tool.run({
            "owner": "microsoft",
            "repo": "vscode"
        })

        # List issues with specific labels
        result = tool.run({
            "owner": "microsoft",
            "repo": "vscode",
            "labels": "bug,help wanted",
            "state": "open"
        })

        # List issues assigned to specific user
        result = tool.run({
            "owner": "microsoft",
            "repo": "vscode",
            "assignee": "username",
            "per_page": 50
        })

        # List all issues (open and closed)
        result = tool.run({
            "owner": "microsoft",
            "repo": "vscode",
            "state": "all"
        })
        ```

    Output Format:
        Returns a list of issue objects containing:
        - Issue number and title
        - Issue state and labels
        - Assignee and milestone information
        - Creation and update timestamps
        - Issue body and comments count
        - Repository and user references

    Error Handling:
        - Repository not found (404)
        - Access denied (403)
        - Authentication errors (401)
        - Invalid filter parameters
        - Network connectivity issues
    """

    name: str = "list_issues"
    description: str = "List issues in a repository with filtering options"
    args_schema: Type[BaseModel] = ListIssuesInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        labels: str = "",
        assignee: str = "",
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        """
        Synchronously list issues in a GitHub repository with filtering options.

        Args:
            owner (str): Repository owner username or organization name.
                       Examples: "microsoft", "octocat", "myorg"
            repo (str): Repository name.
                      Examples: "vscode", "Hello-World", "my-project"
            state (str): Issue state filter.
                       Options: "open" (default), "closed", "all"
            labels (str): Comma-separated list of label names to filter by.
                        Examples: "bug", "bug,help wanted", "enhancement,good first issue"
            assignee (str): GitHub username to filter by assignee.
                          Examples: "username", "octocat"
            per_page (int): Number of issues per page (1-100).
                          Default: 30, Maximum: 100
            run_manager (Optional[CallbackManagerForToolRun]): LangChain callback manager.

        Returns:
            Dict[Any, Any]: JSON response containing filtered issue list.
                           Includes issues matching the specified criteria.

        Raises:
            Exception: For API errors, authentication issues, or network problems.
        """
        endpoint = f"repos/{owner}/{repo}/issues"
        params = {"state": state, "per_page": per_page, "sort": "updated"}
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee"] = assignee
        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        labels: str = "",
        assignee: str = "",
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        """
        Asynchronously list issues in a GitHub repository with filtering options.

        Args:
            owner (str): Repository owner username or organization name.
                       Examples: "microsoft", "octocat", "myorg"
            repo (str): Repository name.
                      Examples: "vscode", "Hello-World", "my-project"
            state (str): Issue state filter.
                       Options: "open" (default), "closed", "all"
            labels (str): Comma-separated list of label names to filter by.
                        Examples: "bug", "bug,help wanted", "enhancement,good first issue"
            assignee (str): GitHub username to filter by assignee.
                          Examples: "username", "octocat"
            per_page (int): Number of issues per page (1-100).
                          Default: 30, Maximum: 100
            run_manager (Optional[AsyncCallbackManagerForToolRun]): LangChain async callback manager.

        Returns:
            Dict[Any, Any]: JSON response containing filtered issue list.
                           Same format as synchronous version.

        Raises:
            Exception: For API errors, authentication issues, or network problems.
        """
        endpoint = f"repos/{owner}/{repo}/issues"
        params = {"state": state, "per_page": per_page, "sort": "updated"}
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee"] = assignee
        return await self.github_client.make_async_request("GET", endpoint, params)


class CreateIssueInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    title: str = Field(description="Issue title")
    body: str = Field(default="", description="Issue description/body")
    labels: List[str] = Field(default=[], description="List of label names")
    assignees: List[str] = Field(default=[], description="List of usernames to assign")


class CreateIssueTool(AbstractTool):
    name: str = "create_issue"
    description: str = "Create a new issue in a repository"
    args_schema: Type[BaseModel] = CreateIssueInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: List[str] = None,
        assignees: List[str] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/issues"
        data = {"title": title, "body": body}
        if labels:
            data["labels"] = labels
        if assignees:
            data["assignees"] = assignees
        return self.github_client.make_request("POST", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: List[str] = None,
        assignees: List[str] = None,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/issues"
        data = {"title": title, "body": body}
        if labels:
            data["labels"] = labels
        if assignees:
            data["assignees"] = assignees
        return await self.github_client.make_async_request("POST", endpoint, data)


class UpdateIssueInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    issue_number: int = Field(description="Issue number")
    title: str = Field(default="", description="New issue title")
    body: str = Field(default="", description="New issue body")
    state: str = Field(default="", description="Issue state: 'open' or 'closed'")
    labels: List[str] = Field(default=[], description="List of label names")


class UpdateIssueTool(AbstractTool):
    name: str = "update_issue"
    description: str = "Update an existing issue"
    args_schema: Type[BaseModel] = UpdateIssueInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        title: str = "",
        body: str = "",
        state: str = "",
        labels: List[str] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/issues/{issue_number}"
        data = {}
        if title:
            data["title"] = title
        if body:
            data["body"] = body
        if state:
            data["state"] = state
        if labels:
            data["labels"] = labels
        return self.github_client.make_request("PATCH", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        title: str = "",
        body: str = "",
        state: str = "",
        labels: List[str] = None,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/issues/{issue_number}"
        data = {}
        if title:
            data["title"] = title
        if body:
            data["body"] = body
        if state:
            data["state"] = state
        if labels:
            data["labels"] = labels
        return await self.github_client.make_async_request("PATCH", endpoint, data)


# ============= PULL REQUESTS MANAGEMENT TOOLS =============
class ListPullRequestsInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    state: str = Field(default="open", description="PR state: 'open', 'closed', 'all'")
    base: str = Field(default="", description="Base branch name")
    head: str = Field(default="", description="Head branch name")
    per_page: int = Field(default=30, description="Number of PRs per page")


class ListPullRequestsTool(AbstractTool):
    name: str = "list_pull_requests"
    description: str = "List pull requests in a repository"
    args_schema: Type[BaseModel] = ListPullRequestsInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        base: str = "",
        head: str = "",
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/pulls"
        params = {"state": state, "per_page": per_page, "sort": "updated"}
        if base:
            params["base"] = base
        if head:
            params["head"] = head
        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        base: str = "",
        head: str = "",
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/pulls"
        params = {"state": state, "per_page": per_page, "sort": "updated"}
        if base:
            params["base"] = base
        if head:
            params["head"] = head
        return await self.github_client.make_async_request("GET", endpoint, params)


class CreatePullRequestInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    title: str = Field(description="Pull request title")
    head: str = Field(description="Branch name containing changes")
    base: str = Field(description="Branch name to merge into")
    body: str = Field(default="", description="Pull request description")
    draft: bool = Field(default=False, description="Create as draft PR")



class CreatePullRequestTool(AbstractTool):
    name: str = "create_pull_request"
    description: str = "Create a new pull request"
    args_schema: Type[BaseModel] = CreatePullRequestInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/pulls"
        data = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        return self.github_client.make_request("POST", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/pulls"
        data = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        return await self.github_client.make_async_request("POST", endpoint, data)

class MergePullRequestInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    pull_number: int = Field(description="Pull request number")
    commit_title: str = Field(default="", description="Commit title for merge")
    commit_message: str = Field(default="", description="Commit message for merge")
    merge_method: str = Field(
        default="merge", description="Merge method: 'merge', 'squash', 'rebase'"
    )


class MergePullRequestTool(AbstractTool):
    name: str = "merge_pull_request"
    description: str = "Merge a pull request"
    args_schema: Type[BaseModel] = MergePullRequestInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        commit_title: str = "",
        commit_message: str = "",
        merge_method: str = "merge",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/pulls/{pull_number}/merge"
        data = {"merge_method": merge_method}
        if commit_title:
            data["commit_title"] = commit_title
        if commit_message:
            data["commit_message"] = commit_message
        return self.github_client.make_request("PUT", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        commit_title: str = "",
        commit_message: str = "",
        merge_method: str = "merge",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/pulls/{pull_number}/merge"
        data = {"merge_method": merge_method}
        if commit_title:
            data["commit_title"] = commit_title
        if commit_message:
            data["commit_message"] = commit_message
        return await self.github_client.make_async_request("PUT", endpoint, data)


class CheckPRStatusInput(BaseModel):
    owner: str = Field(description="Repository owner username or organization name")
    repo: str = Field(description="Repository name")
    pull_number: int = Field(description="Pull request number")


class CheckPRStatusTool(AbstractTool):
    name: str = "check_pr_status"
    description: str = "Check the review status of a pull request using the reviews API"
    args_schema: Type[BaseModel] = CheckPRStatusInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        reviews = self.github_client.make_request("GET", endpoint)
        if "error" in reviews:
            return reviews
        
        if not isinstance(reviews, list):
            return {"error": "Invalid response format from GitHub API", "details": reviews}
            
        approval_states = [r.get("state") for r in reviews]
        is_approved = "APPROVED" in approval_states
        
        return {
            "is_approved": is_approved,
            "reviews": [
                {
                    "id": r.get("id"),
                    "user": r.get("user", {}).get("login"),
                    "state": r.get("state"),
                    "submitted_at": r.get("submitted_at")
                }
                for r in reviews
            ]
        }

    async def _arun(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        reviews = await self.github_client.make_async_request("GET", endpoint)
        if "error" in reviews:
            return reviews
            
        if not isinstance(reviews, list):
            return {"error": "Invalid response format from GitHub API", "details": reviews}
            
        approval_states = [r.get("state") for r in reviews]
        is_approved = "APPROVED" in approval_states
        
        return {
            "is_approved": is_approved,
            "reviews": [
                {
                    "id": r.get("id"),
                    "user": r.get("user", {}).get("login"),
                    "state": r.get("state"),
                    "submitted_at": r.get("submitted_at")
                }
                for r in reviews
            ]
        }


# ... (The rest of the file follows the same pattern) ...
# I will apply the fix to all classes below for completeness.


# ============= WORKFLOWS AND ACTIONS TOOLS =============
class ListWorkflowRunsInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    workflow_id: str = Field(default="", description="Workflow ID or filename")
    branch: str = Field(default="", description="Branch name to filter runs")
    status: str = Field(
        default="", description="Run status: 'completed', 'in_progress', 'queued'"
    )
    per_page: int = Field(default=30, description="Number of runs per page")


class ListWorkflowRunsTool(AbstractTool):
    name: str = "list_workflow_runs"
    description: str = "List workflow runs for a repository"
    args_schema: Type[BaseModel] = ListWorkflowRunsInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        workflow_id: str = "",
        branch: str = "",
        status: str = "",
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        if workflow_id:
            endpoint = f"repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        else:
            endpoint = f"repos/{owner}/{repo}/actions/runs"

        params = {"per_page": per_page}
        if branch:
            params["branch"] = branch
        if status:
            params["status"] = status

        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        owner: str,
        repo: str,
        workflow_id: str = "",
        branch: str = "",
        status: str = "",
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        if workflow_id:
            endpoint = f"repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        else:
            endpoint = f"repos/{owner}/{repo}/actions/runs"

        params = {"per_page": per_page}
        if branch:
            params["branch"] = branch
        if status:
            params["status"] = status

        return await self.github_client.make_async_request("GET", endpoint, params)


class TriggerWorkflowInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    workflow_id: str = Field(description="Workflow ID or filename")
    ref: str = Field(description="Git reference (branch/tag)")
    inputs: Dict[str, Any] = Field(default={}, description="Workflow inputs")


class TriggerWorkflowTool(AbstractTool):
    name: str = "trigger_workflow"
    description: str = "Trigger a workflow dispatch event"
    args_schema: Type[BaseModel] = TriggerWorkflowInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        workflow_id: str,
        ref: str,
        inputs: Dict[str, Any] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
        data = {"ref": ref}
        if inputs:
            data["inputs"] = inputs
        return self.github_client.make_request("POST", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        workflow_id: str,
        ref: str,
        inputs: Dict[str, Any] = None,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
        data = {"ref": ref}
        if inputs:
            data["inputs"] = inputs
        return await self.github_client.make_async_request("POST", endpoint, data)


class CancelWorkflowRunInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    run_id: int = Field(description="Workflow run ID")


class CancelWorkflowRunTool(AbstractTool):
    name: str = "cancel_workflow_run"
    description: str = "Cancel a workflow run"
    args_schema: Type[BaseModel] = CancelWorkflowRunInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        run_id: int,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/actions/runs/{run_id}/cancel"
        return self.github_client.make_request("POST", endpoint)

    async def _arun(
        self,
        owner: str,
        repo: str,
        run_id: int,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/actions/runs/{run_id}/cancel"
        return await self.github_client.make_async_request("POST", endpoint)


# ============= COMMITS AND BRANCHES TOOLS =============
class ListCommitsInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    sha: str = Field(
        default="", description="SHA or branch to start listing commits from"
    )
    path: str = Field(default="", description="Only commits containing this file path")
    author: str = Field(default="", description="GitHub login or email address")
    since: str = Field(default="", description="ISO 8601 date: YYYY-MM-DDTHH:MM:SSZ")
    until: str = Field(default="", description="ISO 8601 date: YYYY-MM-DDTHH:MM:SSZ")
    per_page: int = Field(default=30, description="Number of commits per page")


class ListCommitsTool(AbstractTool):
    name: str = "list_commits"
    description: str = "List commits in a repository"
    args_schema: Type[BaseModel] = ListCommitsInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        sha: str = "",
        path: str = "",
        author: str = "",
        since: str = "",
        until: str = "",
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/commits"
        params = {"per_page": per_page}
        if sha:
            params["sha"] = sha
        if path:
            params["path"] = path
        if author:
            params["author"] = author
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        owner: str,
        repo: str,
        sha: str = "",
        path: str = "",
        author: str = "",
        since: str = "",
        until: str = "",
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/commits"
        params = {"per_page": per_page}
        if sha:
            params["sha"] = sha
        if path:
            params["path"] = path
        if author:
            params["author"] = author
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return await self.github_client.make_async_request("GET", endpoint, params)


class CompareCommitsInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    base: str = Field(description="The base commit SHA or branch/tag name to compare")
    head: str = Field(description="The head commit SHA or branch/tag name to compare")
    per_page: int = Field(default=30, description="Number of commits per page")
    page: int = Field(default=1, description="Page number of the results to fetch")


class CompareCommitsTool(AbstractTool):
    name: str = "compare_commits"
    description: str = "Compare code changes between two commits (or branches/tags) using commit hashes"
    args_schema: Type[BaseModel] = CompareCommitsInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
        per_page: int = 30,
        page: int = 1,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/compare/{base}...{head}"
        params = {"per_page": per_page, "page": page}
        res = self.github_client.make_request("GET", endpoint, params)
        if isinstance(res, dict) and isinstance(res.get("files"), list):
            res["files"] = [f for f in res["files"] if not _is_lock_file(f.get("filename", ""))]
        return res

    async def _arun(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
        per_page: int = 30,
        page: int = 1,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/compare/{base}...{head}"
        params = {"per_page": per_page, "page": page}
        res = await self.github_client.make_async_request("GET", endpoint, params)
        if isinstance(res, dict) and isinstance(res.get("files"), list):
            res["files"] = [f for f in res["files"] if not _is_lock_file(f.get("filename", ""))]
        return res


class ListBranchesInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    protected: bool = Field(default=False, description="Filter by protection status")
    per_page: int = Field(default=30, description="Number of branches per page")


class ListBranchesTool(AbstractTool):
    name: str = "list_branches"
    description: str = "List branches in a repository"
    args_schema: Type[BaseModel] = ListBranchesInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        protected: bool = False,
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/branches"
        params = {"per_page": per_page}
        if protected:
            params["protected"] = "true"
        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        owner: str,
        repo: str,
        protected: bool = False,
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/branches"
        params = {"per_page": per_page}
        if protected:
            params["protected"] = "true"
        return await self.github_client.make_async_request("GET", endpoint, params)


class CreateBranchInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    branch_name: str = Field(description="Name of the new branch to create")
    source_branch: str = Field(
        default="main",
        description="Name of the source branch to branch off from (e.g. 'main')"
    )
    sha: str = Field(
        default="",
        description="Optional: Specific commit SHA to branch off from. If provided, overrides source_branch."
    )


class CreateBranchTool(AbstractTool):
    name: str = "create_branch"
    description: str = "Create a new branch in a repository from a source branch or commit SHA"
    args_schema: Type[BaseModel] = CreateBranchInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        source_branch: str = "main",
        sha: str = "",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        if not sha:
            # Clean source branch name if it starts with refs/heads/
            clean_source = source_branch
            if clean_source.startswith("refs/heads/"):
                clean_source = clean_source[len("refs/heads/"):]
            elif clean_source.startswith("heads/"):
                clean_source = clean_source[len("heads/"):]

            # Get the commit SHA of the source branch
            ref_endpoint = f"repos/{owner}/{repo}/git/ref/heads/{clean_source}"
            ref_data = self.github_client.make_request("GET", ref_endpoint)
            if "error" in ref_data:
                return ref_data
            if "object" not in ref_data or "sha" not in ref_data["object"]:
                return {"error": f"Could not find source branch '{source_branch}' or its commit SHA."}
            sha = ref_data["object"]["sha"]

        # Ensure branch_name starts with refs/heads/
        ref = branch_name
        if not ref.startswith("refs/"):
            if not ref.startswith("heads/"):
                ref = f"refs/heads/{ref}"
            else:
                ref = f"refs/{ref}"

        endpoint = f"repos/{owner}/{repo}/git/refs"
        data = {"ref": ref, "sha": sha}
        return self.github_client.make_request("POST", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        source_branch: str = "main",
        sha: str = "",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        if not sha:
            # Clean source branch name if it starts with refs/heads/
            clean_source = source_branch
            if clean_source.startswith("refs/heads/"):
                clean_source = clean_source[len("refs/heads/"):]
            elif clean_source.startswith("heads/"):
                clean_source = clean_source[len("heads/"):]

            # Get the commit SHA of the source branch
            ref_endpoint = f"repos/{owner}/{repo}/git/ref/heads/{clean_source}"
            ref_data = await self.github_client.make_async_request("GET", ref_endpoint)
            if "error" in ref_data:
                return ref_data
            if "object" not in ref_data or "sha" not in ref_data["object"]:
                return {"error": f"Could not find source branch '{source_branch}' or its commit SHA."}
            sha = ref_data["object"]["sha"]

        # Ensure branch_name starts with refs/heads/
        ref = branch_name
        if not ref.startswith("refs/"):
            if not ref.startswith("heads/"):
                ref = f"refs/heads/{ref}"
            else:
                ref = f"refs/{ref}"

        endpoint = f"repos/{owner}/{repo}/git/refs"
        data = {"ref": ref, "sha": sha}
        return await self.github_client.make_async_request("POST", endpoint, data)


# ============= DEPLOYMENT AND ENVIRONMENTS TOOLS =============
class ListDeploymentsInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    sha: str = Field(default="", description="SHA to filter deployments")
    ref: str = Field(default="", description="Name of ref to filter deployments")
    task: str = Field(default="", description="Task to filter deployments")
    environment: str = Field(
        default="", description="Environment to filter deployments"
    )
    per_page: int = Field(default=30, description="Number of deployments per page")


class ListDeploymentsTool(AbstractTool):
    name: str = "list_deployments"
    description: str = "List deployments for a repository"
    args_schema: Type[BaseModel] = ListDeploymentsInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        sha: str = "",
        ref: str = "",
        task: str = "",
        environment: str = "",
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/deployments"
        params = {"per_page": per_page}
        if sha:
            params["sha"] = sha
        if ref:
            params["ref"] = ref
        if task:
            params["task"] = task
        if environment:
            params["environment"] = environment
        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        owner: str,
        repo: str,
        sha: str = "",
        ref: str = "",
        task: str = "",
        environment: str = "",
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/deployments"
        params = {"per_page": per_page}
        if sha:
            params["sha"] = sha
        if ref:
            params["ref"] = ref
        if task:
            params["task"] = task
        if environment:
            params["environment"] = environment
        return await self.github_client.make_async_request("GET", endpoint, params)


class CreateDeploymentInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    ref: str = Field(description="Git reference to deploy")
    task: str = Field(default="deploy", description="Task name for deployment")
    auto_merge: bool = Field(default=True, description="Auto merge default branch")
    required_contexts: List[str] = Field(
        default=[], description="Required status contexts"
    )
    payload: Dict[str, Any] = Field(default={}, description="JSON payload")
    environment: str = Field(default="production", description="Target environment")
    description: str = Field(default="", description="Deployment description")


class CreateDeploymentTool(AbstractTool):
    name: str = "create_deployment"
    description: str = "Create a new deployment"
    args_schema: Type[BaseModel] = CreateDeploymentInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        ref: str,
        task: str = "deploy",
        auto_merge: bool = True,
        required_contexts: List[str] = None,
        payload: Dict[str, Any] = None,
        environment: str = "production",
        description: str = "",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/deployments"
        data = {
            "ref": ref,
            "task": task,
            "auto_merge": auto_merge,
            "environment": environment,
        }
        if required_contexts:
            data["required_contexts"] = required_contexts
        if payload:
            data["payload"] = payload
        if description:
            data["description"] = description
        return self.github_client.make_request("POST", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        ref: str,
        task: str = "deploy",
        auto_merge: bool = True,
        required_contexts: List[str] = None,
        payload: Dict[str, Any] = None,
        environment: str = "production",
        description: str = "",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/deployments"
        data = {
            "ref": ref,
            "task": task,
            "auto_merge": auto_merge,
            "environment": environment,
        }
        if required_contexts:
            data["required_contexts"] = required_contexts
        if payload:
            data["payload"] = payload
        if description:
            data["description"] = description
        return await self.github_client.make_async_request("POST", endpoint, data)


# ============= REPOSITORY CONTENT TOOLS =============
class GetFileContentInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    path: str = Field(description="File path in repository")
    ref: str = Field(
        default="",
        description="Branch, tag, or commit SHA (defaults to default branch)",
    )


class GetFileContentTool(AbstractTool):
    name: str = "get_file_content"
    description: str = "Get the content of a file from a repository"
    args_schema: Type[BaseModel] = GetFileContentInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str = "",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        if _is_lock_file(path):
            return {"error": f"Reading lock files is not allowed: {path}"}
        endpoint = f"repos/{owner}/{repo}/contents/{path}"
        params = {}
        if ref:
            params["ref"] = ref
        res = self.github_client.make_request("GET", endpoint, params)
        if isinstance(res, dict) and res.get("encoding") == "base64" and "content" in res:
            try:
                import base64
                clean_content = res["content"].replace("\n", "").replace("\r", "")
                return base64.b64decode(clean_content).decode("utf-8", errors="replace")
            except Exception:
                pass
        return res

    async def _arun(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str = "",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        if _is_lock_file(path):
            return {"error": f"Reading lock files is not allowed: {path}"}
        if isinstance(res, dict) and res.get("encoding") == "base64" and "content" in res:
            try:
                import base64
                clean_content = res["content"].replace("\n", "").replace("\r", "")
                return base64.b64decode(clean_content).decode("utf-8", errors="replace")
            except Exception:
                pass
        return res


class CreateOrUpdateFileInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    path: str = Field(description="File path in repository")
    message: str = Field(description="Commit message")
    content: str = Field(description="Base64 encoded file content")
    sha: str = Field(
        default="", description="SHA of existing file (required for updates)"
    )
    branch: str = Field(
        default="", description="Branch name (defaults to default branch)"
    )
    committer_name: str = Field(default="", description="Committer name")
    committer_email: str = Field(default="", description="Committer email")


class CreateOrUpdateFileTool(AbstractTool):
    name: str = "create_or_update_file"
    description: str = "Create or update a file in a repository"
    args_schema: Type[BaseModel] = CreateOrUpdateFileInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        path: str,
        message: str,
        content: str,
        sha: str = "",
        branch: str = "",
        committer_name: str = "",
        committer_email: str = "",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/contents/{path}"
        data = {"message": message, "content": content}
        if sha:
            data["sha"] = sha
        if branch:
            data["branch"] = branch
        if committer_name and committer_email:
            data["committer"] = {"name": committer_name, "email": committer_email}
        return self.github_client.make_request("PUT", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        path: str,
        message: str,
        content: str,
        sha: str = "",
        branch: str = "",
        committer_name: str = "",
        committer_email: str = "",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/contents/{path}"
        data = {"message": message, "content": content}
        if sha:
            data["sha"] = sha
        if branch:
            data["branch"] = branch
        if committer_name and committer_email:
            data["committer"] = {"name": committer_name, "email": committer_email}
        return await self.github_client.make_async_request("PUT", endpoint, data)


# ============= WEBHOOKS TOOLS =============
class ListWebhooksInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    per_page: int = Field(default=30, description="Number of webhooks per page")


class ListWebhooksTool(AbstractTool):
    name: str = "list_webhooks"
    description: str = "List webhooks for a repository"
    args_schema: Type[BaseModel] = ListWebhooksInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/hooks"
        params = {"per_page": per_page}
        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        owner: str,
        repo: str,
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/hooks"
        params = {"per_page": per_page}
        return await self.github_client.make_async_request("GET", endpoint, params)


class CreateWebhookInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    url: str = Field(description="Webhook URL")
    events: List[str] = Field(
        default=["push"], description="List of events to trigger webhook"
    )
    active: bool = Field(default=True, description="Whether webhook is active")
    secret: str = Field(default="", description="Webhook secret for validation")


class CreateWebhookTool(AbstractTool):
    name: str = "create_webhook"
    description: str = "Create a webhook for a repository"
    args_schema: Type[BaseModel] = CreateWebhookInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        url: str,
        events: List[str] = None,
        active: bool = True,
        secret: str = "",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/hooks"
        config = {"url": url, "content_type": "json"}
        if secret:
            config["secret"] = secret

        data = {
            "name": "web",
            "active": active,
            "events": events or ["push"],
            "config": config,
        }
        return self.github_client.make_request("POST", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        url: str,
        events: List[str] = None,
        active: bool = True,
        secret: str = "",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = f"repos/{owner}/{repo}/hooks"
        config = {"url": url, "content_type": "json"}
        if secret:
            config["secret"] = secret

        data = {
            "name": "web",
            "active": active,
            "events": events or ["push"],
            "config": config,
        }
        return await self.github_client.make_async_request("POST", endpoint, data)


# ============= SEARCH TOOLS =============
class SearchRepositoriesInput(BaseModel):
    query: str = Field(description="Search query")
    sort: str = Field(
        default="",
        description="Sort by: 'stars', 'forks', 'help-wanted-issues', 'updated'",
    )
    order: str = Field(default="desc", description="Sort order: 'asc' or 'desc'")
    per_page: int = Field(default=30, description="Number of results per page")


class SearchRepositoriesTool(AbstractTool):
    name: str = "search_repositories"
    description: str = "Search for repositories on GitHub"
    args_schema: Type[BaseModel] = SearchRepositoriesInput
    github_client: GitHubAPIClient

    def _run(
        self,
        query: str,
        sort: str = "",
        order: str = "desc",
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = "search/repositories"
        params = {"q": query, "per_page": per_page, "order": order}
        if sort:
            params["sort"] = sort
        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        query: str,
        sort: str = "",
        order: str = "desc",
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = "search/repositories"
        params = {"q": query, "per_page": per_page, "order": order}
        if sort:
            params["sort"] = sort
        return await self.github_client.make_async_request("GET", endpoint, params)


class SearchIssuesInput(BaseModel):
    query: str = Field(description="Search query")
    sort: str = Field(
        default="", description="Sort by: 'comments', 'reactions', 'created', 'updated'"
    )
    order: str = Field(default="desc", description="Sort order: 'asc' or 'desc'")
    per_page: int = Field(default=30, description="Number of results per page")


class SearchIssuesTool(AbstractTool):
    name: str = "search_issues"
    description: str = "Search for issues and pull requests on GitHub"
    args_schema: Type[BaseModel] = SearchIssuesInput
    github_client: GitHubAPIClient

    def _run(
        self,
        query: str,
        sort: str = "",
        order: str = "desc",
        per_page: int = 30,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        endpoint = "search/issues"
        params = {"q": query, "per_page": per_page, "order": order}
        if sort:
            params["sort"] = sort
        return self.github_client.make_request("GET", endpoint, params)

    async def _arun(
        self,
        query: str,
        sort: str = "",
        order: str = "desc",
        per_page: int = 30,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        endpoint = "search/issues"
        params = {"q": query, "per_page": per_page, "order": order}
        if sort:
            params["sort"] = sort
        return await self.github_client.make_async_request("GET", endpoint, params)

        
class RequestReviewerInput(BaseModel):
    """Input schema for requesting reviewers on a pull request."""
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    pull_number: int = Field(description="Pull request number")
    reviewers: List[str] = Field(description="List of GitHub usernames to request as reviewers")


class RequestReviewerTool(AbstractTool):
    """Tool to request reviewers for a pull request."""
    name: str = "request_reviewer"
    description: str = "Request reviewers for a pull request"
    args_schema: Type[BaseModel] = RequestReviewerInput
    github_client: GitHubAPIClient
    
    def _run(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        reviewers: List[str],
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        """Run the tool."""
        endpoint = f"repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers"
        data = {"reviewers": reviewers}
        return self.github_client.make_request("POST", endpoint, data)

    async def _arun(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        reviewers: List[str],
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        """Asynchronously request reviewers for a pull request."""
        endpoint = f"repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers"
        data = {"reviewers": reviewers}
        return await self.github_client.make_async_request("POST", endpoint, data)


class GetTreeInput(BaseModel):
    owner: str = Field(description="Repository owner username")
    repo: str = Field(description="Repository name")
    branch: str = Field(
        default="",
        description="Branch name (defaults to repository's default branch)",
    )


class GetTreeTool(AbstractTool):
    name: str = "get_tree"
    description: str = (
        "Recursively fetches the entire tree structure of the codebase, "
        "whether directories or files, and retrieves relative path of each directory or file path."
    )
    args_schema: Type[BaseModel] = GetTreeInput
    github_client: GitHubAPIClient

    def _run(
        self,
        owner: str,
        repo: str,
        branch: str = "",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ):
        if not branch:
            repo_info = self.github_client.make_request("GET", f"repos/{owner}/{repo}")
            if "error" in repo_info:
                return repo_info
            branch = repo_info.get("default_branch", "main")

        endpoint = f"repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        tree_response = self.github_client.make_request("GET", endpoint)
        if "error" in tree_response:
            return tree_response

        results = []
        for entry in tree_response.get("tree", []):
            if _is_lock_file(entry.get("path", "")):
                continue
            item = {
                "path": entry.get("path"),
                "type": entry.get("type"),
            }
            if "sha" in entry:
                item["sha"] = entry["sha"]
            if "size" in entry:
                item["size"] = entry["size"]
            results.append(item)
        return results

    async def _arun(
        self,
        owner: str,
        repo: str,
        branch: str = "",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ):
        if not branch:
            repo_info = await self.github_client.make_async_request("GET", f"repos/{owner}/{repo}")
            if "error" in repo_info:
                return repo_info
            branch = repo_info.get("default_branch", "main")

        endpoint = f"repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        tree_response = await self.github_client.make_async_request("GET", endpoint)
        if "error" in tree_response:
            return tree_response

        results = []
        for entry in tree_response.get("tree", []):
            if _is_lock_file(entry.get("path", "")):
                continue
            item = {
                "path": entry.get("path"),
                "type": entry.get("type"),
            }
            if "sha" in entry:
                item["sha"] = entry["sha"]
            if "size" in entry:
                item["size"] = entry["size"]
            results.append(item)
        return results

# class ListCommitsToolResponsePayload(BaseModel):

__all__ = [
    "GitHubAPIClient",
    "GitHubAppPermission",
    "GITHUB_APP_PERMISSION_CATEGORIES",
    "GITHUB_TOOL_PERMISSIONS",
    "describe_github_app_permissions",
    "permissions_for_tool",
    "ListRepositoriesTool",
    "GetRepositoryTool",
    "ListIssuesTool",
    "CreateIssueTool",
    "UpdateIssueTool",
    "ListPullRequestsTool",
    "CreatePullRequestTool",
    "MergePullRequestTool",
    "CheckPRStatusTool",
    "ListWorkflowRunsTool",
    "TriggerWorkflowTool",
    "CancelWorkflowRunTool",
    "ListCommitsTool",
    "CompareCommitsTool",
    "ListBranchesTool",
    "CreateBranchTool",
    "ListDeploymentsTool",
    "CreateDeploymentTool",
    "GetFileContentTool",
    "CreateOrUpdateFileTool",
    "ListWebhooksTool",
    "CreateWebhookTool",
    "SearchRepositoriesTool",
    "SearchIssuesTool",
    "RequestReviewerTool",
    "GetTreeTool",
]


if __name__ == "__main__":
    github_client = GitHubAPIClient()

    tool_1 = ListRepositoriesTool(github_client=github_client)
    tool_2 = GetRepositoryTool(github_client=github_client)
    tool_3 = ListBranchesTool(github_client=github_client)
    tool_4 = GetTreeTool(github_client=github_client)
    tool_5 = GetFileContentTool(github_client=github_client)
    tool_6 = ListWorkflowRunsTool(github_client=github_client)
    tool_7 = ListCommitsTool(github_client=github_client)
    tool_8 = CompareCommitsTool(github_client=github_client)
    
    res_1 = tool_1.invoke({
        "owner":"synchropia",
        "repo_type":"all"
    })
    print(f"these are the two repo names and owner: {res_1[0].name},{res_1[0].owner}, {res_1[1].name},{res_1[1].owner}")

    res_2 = tool_2.invoke({
        "owner":res_1[0].owner,
        "repo":res_1[0].name
    })

    res_3 = tool_3.invoke({
        "owner":res_1[1].owner,
        "repo":res_1[1].name
    })
    branches = [branch["name"] for branch in res_3]


    res_4 = tool_4.invoke({
        "owner":res_1[1].owner,
        "repo":res_1[1].name,
        "branch":branches[1]
    })
    file_paths = [item["path"] for item in res_4]
    # print(file_paths)

    README_FILE_CONTENT = tool_5.invoke({
        "owner":res_1[1].owner,
        "repo":res_1[1].name,
        "path":file_paths[5],
        "ref":branches[1]
    })
    # print(README_FILE_CONTENT)

    res_6 = tool_6.invoke({
        "owner":res_1[1].owner,
        "repo":res_1[1].name,
        "ref":branches[1]
    })
    runs = res_6["workflow_runs"]
    run = runs[0]
    # print({
    #     "id":          run.get("id"),
    #     "name":        run.get("name"),
    #     "workflow_id": run.get("workflow_id"),
    #     "path":        run.get("path"),
    #     "event":       run.get("event"),
    #     "status":      run.get("status"),
    #     "conclusion":  run.get("conclusion"),
    #     "branch":      run.get("head_branch"),
    #     "sha":         (run.get("head_sha") or "")[:8],
    #     "run_number":  run.get("run_number"),
    #     "created_at":  run.get("created_at"),
    #     "html_url":    run.get("html_url"),
    #     "cancel_url":  run.get("cancel_url"),
    # })
    res_7 = tool_7.invoke({
        "owner":res_1[1].owner,
        "repo":res_1[1].name
    })

    head_sha = res_7[0]["sha"]      # newest commit on the page = HEAD
    base_sha = res_7[-1]["sha"]     # oldest commit on the page = base
    compare_args = {
        "owner": res_1[1].owner,
        "repo": res_1[1].name,
        "base": base_sha,
        "head": head_sha,
        "per_page": 30,
        "page": 1,
    }
    res_8 = tool_8.invoke(compare_args)
    for k, v in res_8.items():
        print(k)
    # print(
    #     res_8["url"],
    #     res_8["html_url"],
    #     res_8["permalink_url"],
    #     res_8["diff_url"],
    #     res_8["patch_url"],
    #     res_8["base_commit"],
    #     res_8["merge_base_commit"],
    #     res_8["status"],
    #     res_8["ahead_by"],
    #     res_8["behind_by"],
    #     res_8["total_commits"],
    #     res_8["commits"],
    #     res_8["files"],
    # )
    
