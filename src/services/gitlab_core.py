import os
import json
import urllib.parse
from pydantic import BaseModel, Field, EmailStr
from typing import Dict, Any, Optional, List, Type
import aiohttp
import sys
import httpx
import requests
import time
import jwt
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from dotenv import load_dotenv

load_dotenv()

from abc import ABC, abstractmethod
from langchain_core.tools import BaseTool
from src.utils.logger import logger

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


class GitLabAPIClient:
    """
    A comprehensive client for interacting with the GitLab REST API v4.
    
    Provides both synchronous and asynchronous methods for making requests.
    Supports Personal Access Token (PRIVATE-TOKEN) and OAuth2 Bearer token authentication.
    """

    def __init__(
        self,
        private_token: Optional[str] = None,
        oauth_token: Optional[str] = None,
        base_url: str = "https://gitlab.com/api/v4"
    ):
        self.private_token = private_token or os.getenv("GITLAB_PRIVATE_TOKEN") or os.getenv("GITLAB_PERSONAL_ACCESS_TOKEN")
        self.oauth_token = oauth_token or os.getenv("GITLAB_OAUTH_TOKEN")
        self.base_url = base_url.rstrip("/")

        self.headers = {
            "Accept": "application/json",
            "User-Agent": "DevOps-Agent/1.0",
        }

        if self.private_token:
            self.headers["PRIVATE-TOKEN"] = self.private_token
        elif self.oauth_token:
            self.headers["Authorization"] = f"Bearer {self.oauth_token}"
        else:
            logger.warning(
                "No GitLab credentials provided. Client will perform unauthenticated requests."
            )

        self.sync_client = httpx.Client(headers=self.headers, timeout=30.0)
        self.async_client = httpx.AsyncClient(headers=self.headers, timeout=30.0)
        logger.info(f"GitLabAPIClient initialized with base_url: {self.base_url}")

    def __del__(self):
        try:
            self.sync_client.close()
        except Exception:
            pass

    async def aclose(self):
        try:
            await self.async_client.aclose()
        except Exception:
            pass

    def make_request(
        self, method: str, endpoint: str, data: Dict[Any, Any] = None, params: Dict[str, Any] = None
    ) -> Dict[Any, Any]:
        """Make a synchronous HTTP request to the GitLab API."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        logger.info(f"Making {method} request to: {url} (reusing connection pool)")
        if data:
            logger.debug(f"Request data payload: {data}")
        if params:
            logger.debug(f"Request query parameters: {params}")
        
        try:
            if method.upper() in ["POST", "PUT", "PATCH"] and data:
                response = self.sync_client.request(method, url, json=data, params=params)
            else:
                response = self.sync_client.request(method, url, params=params)

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
            logger.error(f"GitLab request to {url} failed: {str(e)}")
            try:
                error_detail = e.response.json()
            except ValueError:
                error_detail = e.response.text
            return {"error": f"Request failed: {str(e)}", "details": error_detail, "status_code": e.response.status_code}
        except httpx.RequestError as e:
            logger.error(f"GitLab network request to {url} failed: {str(e)}")
            return {"error": f"Network request failed: {str(e)}"}

    async def make_async_request(
        self, method: str, endpoint: str, data: Dict[Any, Any] = None, params: Dict[str, Any] = None
    ) -> Dict[Any, Any]:
        """Make an asynchronous HTTP request to the GitLab API."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        logger.info(f"Making async {method} request to: {url} (reusing connection pool)")
        if data:
            logger.debug(f"Async request data payload: {data}")
        if params:
            logger.debug(f"Async request query parameters: {params}")
        
        try:
            if method.upper() in ["POST", "PUT", "PATCH"] and data:
                response = await self.async_client.request(method, url, json=data, params=params)
            else:
                response = await self.async_client.request(method, url, params=params)

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
            logger.error(f"Async GitLab request to {url} failed: {str(e)}")
            try:
                error_detail = e.response.json()
            except ValueError:
                error_detail = e.response.text
            return {"error": f"Request failed: {str(e)}", "details": error_detail, "status_code": e.response.status_code}
        except httpx.RequestError as e:
            logger.error(f"Async GitLab network request to {url} failed: {str(e)}")
            return {"error": f"Network request failed: {str(e)}"}


# =====================================================================
# GitLab SWARM AGENT TOOLS
# =====================================================================

class ListGitLabProjectsInput(BaseModel):
    search: Optional[str] = Field(None, description="Search term to filter projects by name.")
    owned: Optional[bool] = Field(True, description="Limit to projects owned by the authenticated user.")

class ListGitLabProjectsTool(AbstractTool):
    name: str = "gitlab_list_projects"
    description: str = "List projects (repositories) available on the authenticated GitLab account."
    args_schema: Type[BaseModel] = ListGitLabProjectsInput
    gitlab_client: Optional[GitLabAPIClient] = None

    def _get_client(self) -> GitLabAPIClient:
        return self.gitlab_client or GitLabAPIClient()

    def _run(self, search: Optional[str] = None, owned: Optional[bool] = True, run_manager: Optional[CallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        params = {"owned": str(owned).lower()}
        if search:
            params["search"] = search
        return client.make_request("GET", "projects", params=params)

    async def _arun(self, search: Optional[str] = None, owned: Optional[bool] = True, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        params = {"owned": str(owned).lower()}
        if search:
            params["search"] = search
        return await client.make_async_request("GET", "projects", params=params)


class GetGitLabFileContentInput(BaseModel):
    project_id: str = Field(..., description="The ID or URL-encoded path of the project (e.g. 12345 or 'group/project').")
    file_path: str = Field(..., description="The path of the file to retrieve.")
    ref: Optional[str] = Field("main", description="The name of branch, tag or commit hash.")

class GetGitLabFileContentTool(AbstractTool):
    name: str = "gitlab_get_file_content"
    description: str = "Retrieve the raw text content of a file from a GitLab project."
    args_schema: Type[BaseModel] = GetGitLabFileContentInput
    gitlab_client: Optional[GitLabAPIClient] = None

    def _get_client(self) -> GitLabAPIClient:
        return self.gitlab_client or GitLabAPIClient()

    def _run(self, project_id: str, file_path: str, ref: Optional[str] = "main", run_manager: Optional[CallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        encoded_project_id = urllib.parse.quote(project_id, safe='')
        encoded_file_path = urllib.parse.quote(file_path, safe='')
        endpoint = f"projects/{encoded_project_id}/repository/files/{encoded_file_path}/raw"
        params = {"ref": ref}
        
        # Raw endpoint returns plain text.
        # We handle plain text response natively here.
        url = f"{client.base_url}/{endpoint}"
        logger.info(f"Fetching raw file content from {url} ref {ref}")
        try:

            response = client.sync_client.get(url, params=params)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch GitLab file content: {e}")
            return {"error": f"Failed to retrieve file: {str(e)}"}

    async def _arun(self, project_id: str, file_path: str, ref: Optional[str] = "main", run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        encoded_project_id = urllib.parse.quote(project_id, safe='')
        encoded_file_path = urllib.parse.quote(file_path, safe='')
        endpoint = f"projects/{encoded_project_id}/repository/files/{encoded_file_path}/raw"
        params = {"ref": ref}
        
        url = f"{client.base_url}/{endpoint}"
        logger.info(f"Fetching raw file content asynchronously from {url} ref {ref}")
        try:
            response = await client.async_client.get(url, params=params)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch async GitLab file content: {e}")
            return {"error": f"Failed to retrieve file: {str(e)}"}


class CreateOrUpdateGitLabFileInput(BaseModel):
    project_id: str = Field(..., description="The ID or URL-encoded path of the project (e.g. 12345 or 'group/project').")
    file_path: str = Field(..., description="The path of the file to create or update.")
    content: str = Field(..., description="The content to write to the file.")
    commit_message: str = Field(..., description="Commit message describing the changes.")
    branch: Optional[str] = Field("main", description="The name of branch to commit to.")

class CreateOrUpdateGitLabFileTool(AbstractTool):
    name: str = "gitlab_create_or_update_file"
    description: str = "Create a new file or update an existing file's content in a GitLab repository."
    args_schema: Type[BaseModel] = CreateOrUpdateGitLabFileInput
    gitlab_client: Optional[GitLabAPIClient] = None

    def _get_client(self) -> GitLabAPIClient:
        return self.gitlab_client or GitLabAPIClient()

    def _run(self, project_id: str, file_path: str, content: str, commit_message: str, branch: Optional[str] = "main", run_manager: Optional[CallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        encoded_project_id = urllib.parse.quote(project_id, safe='')
        encoded_file_path = urllib.parse.quote(file_path, safe='')
        endpoint = f"projects/{encoded_project_id}/repository/files/{encoded_file_path}"
        
        payload = {
            "branch": branch,
            "content": content,
            "commit_message": commit_message
        }
        
        # Check if file exists first to determine if we perform a PUT (update) or POST (create)
        check_endpoint = f"projects/{encoded_project_id}/repository/files/{encoded_file_path}"
        check_response = client.make_request("GET", check_endpoint, params={"ref": branch})
        
        if "error" in check_response and check_response.get("status_code") == 404:
            # File doesn't exist, create it
            logger.info(f"File '{file_path}' does not exist in branch '{branch}'. Performing create...")
            return client.make_request("POST", endpoint, data=payload)
        elif "error" in check_response:
            return check_response
        else:
            # File exists, update it
            logger.info(f"File '{file_path}' exists in branch '{branch}'. Performing update...")
            return client.make_request("PUT", endpoint, data=payload)

    async def _arun(self, project_id: str, file_path: str, content: str, commit_message: str, branch: Optional[str] = "main", run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        encoded_project_id = urllib.parse.quote(project_id, safe='')
        encoded_file_path = urllib.parse.quote(file_path, safe='')
        endpoint = f"projects/{encoded_project_id}/repository/files/{encoded_file_path}"
        
        payload = {
            "branch": branch,
            "content": content,
            "commit_message": commit_message
        }
        
        check_endpoint = f"projects/{encoded_project_id}/repository/files/{encoded_file_path}"
        check_response = await client.make_async_request("GET", check_endpoint, params={"ref": branch})
        
        if "error" in check_response and check_response.get("status_code") == 404:
            logger.info(f"File '{file_path}' does not exist in branch '{branch}'. Performing async create...")
            return await client.make_async_request("POST", endpoint, data=payload)
        elif "error" in check_response:
            return check_response
        else:
            logger.info(f"File '{file_path}' exists in branch '{branch}'. Performing async update...")
            return await client.make_async_request("PUT", endpoint, data=payload)


class CreateGitLabBranchInput(BaseModel):
    project_id: str = Field(..., description="The ID or URL-encoded path of the project.")
    branch: str = Field(..., description="The name of the new branch to create.")
    ref: str = Field("main", description="The branch name, tag or commit hash to create the branch from.")

class CreateGitLabBranchTool(AbstractTool):
    name: str = "gitlab_create_branch"
    description: str = "Create a new branch in a GitLab repository."
    args_schema: Type[BaseModel] = CreateGitLabBranchInput
    gitlab_client: Optional[GitLabAPIClient] = None

    def _get_client(self) -> GitLabAPIClient:
        return self.gitlab_client or GitLabAPIClient()

    def _run(self, project_id: str, branch: str, ref: str = "main", run_manager: Optional[CallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        encoded_project_id = urllib.parse.quote(project_id, safe='')
        endpoint = f"projects/{encoded_project_id}/repository/branches"
        payload = {
            "branch": branch,
            "ref": ref
        }
        return client.make_request("POST", endpoint, data=payload)

    async def _arun(self, project_id: str, branch: str, ref: str = "main", run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        encoded_project_id = urllib.parse.quote(project_id, safe='')
        endpoint = f"projects/{encoded_project_id}/repository/branches"
        payload = {
            "branch": branch,
            "ref": ref
        }
        return await client.make_async_request("POST", endpoint, data=payload)


class CreateGitLabMergeRequestInput(BaseModel):
    project_id: str = Field(..., description="The ID or URL-encoded path of the project.")
    source_branch: str = Field(..., description="The source branch containing changes.")
    target_branch: str = Field("main", description="The target branch to merge into.")
    title: str = Field(..., description="Title of the merge request.")
    description: Optional[str] = Field(None, description="Description of the merge request.")

class CreateGitLabMergeRequestTool(AbstractTool):
    name: str = "gitlab_create_merge_request"
    description: str = "Create a new Merge Request (MR) in a GitLab repository."
    args_schema: Type[BaseModel] = CreateGitLabMergeRequestInput
    gitlab_client: Optional[GitLabAPIClient] = None

    def _get_client(self) -> GitLabAPIClient:
        return self.gitlab_client or GitLabAPIClient()

    def _run(self, project_id: str, source_branch: str, target_branch: str = "main", title: str = "", description: Optional[str] = None, run_manager: Optional[CallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        encoded_project_id = urllib.parse.quote(project_id, safe='')
        endpoint = f"projects/{encoded_project_id}/merge_requests"
        payload = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
        }
        if description:
            payload["description"] = description
        return client.make_request("POST", endpoint, data=payload)

    async def _arun(self, project_id: str, source_branch: str, target_branch: str = "main", title: str = "", description: Optional[str] = None, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> Any:
        client = self._get_client()
        encoded_project_id = urllib.parse.quote(project_id, safe='')
        endpoint = f"projects/{encoded_project_id}/merge_requests"
        payload = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
        }
        if description:
            payload["description"] = description
        return await client.make_async_request("POST", endpoint, data=payload)

__all__ = [
    "GitLabAPIClient",
    "ListGitLabProjectsTool",
    "GetGitLabFileContentTool",
    "CreateOrUpdateGitLabFileTool",
    "CreateGitLabBranchTool",
    "CreateGitLabMergeRequestTool",
]


if __name__ == "__main__":
    # Test basic functionality
    client = GitLabAPIClient()
    
    tool = ListGitLabProjectsTool(gitlab_client=client)
    projects = tool.invoke({
        "owned": True,
        "search": "Synchropia"
    })
    print(projects)
    tools_2 = GetGitLabFileContentTool(gitlab_client=client)
    file_content = tools_2.invoke({
        "project_id": "83721178",
        "file_path": "src/services/redis_client.py",
        "branch": "semantic_cache"
    })
    print(file_content)
    client.sync_client.close()

