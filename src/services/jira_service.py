import base64
import json
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Type
import aiohttp
import requests
import httpx
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, Field
from src.config.settings import settings
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool

load_dotenv()

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


class JiraAPIClient:
    """A comprehensive client for interacting with the Jira Cloud REST API.
    
    Provides both synchronous and asynchronous methods for making requests to Jira,
    utilizing persistent HTTPX clients for TCP connection pooling.
    """

    def __init__(self, jira_url: Optional[str] = None, email: Optional[str] = None, api_token: Optional[str] = None):
        self.jira_url = jira_url or settings.JIRA_URL
        self.email = email or settings.JIRA_EMAIL
        self.api_token = api_token or settings.JIRA_API_TOKEN

        if self.jira_url:
            self.jira_url = self.jira_url.strip('"\' ').rstrip("/")
        if self.email:
            self.email = self.email.strip('"\' ')
        if self.api_token:
            self.api_token = self.api_token.strip('"\' ')

        if not self.jira_url or not self.email or not self.api_token:
            raise ValueError("Jira URL, email, and API token must be configured in environment or passed directly.")

        # Encode credentials for basic auth
        auth_str = f"{self.email}:{self.api_token}"
        auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        
        self.headers = {
            "Authorization": f"Basic {auth_b64}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        # Persistent clients for TCP Keep-Alive
        self.sync_client = httpx.Client(headers=self.headers, timeout=30.0)
        self.async_client = httpx.AsyncClient(headers=self.headers, timeout=30.0)
        logger.success("Jira API client initialized successfully with persistent TCP connection pools.")

    def __del__(self):
        # Close connection pools when garbage collected
        try:
            self.sync_client.close()
        except Exception:
            pass

    def make_request(
        self, method: str, path: str, data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.jira_url}/{path.lstrip('/')}"
        logger.info(f"Making Jira {method} request to: {url} (reusing connection pool)")
        try:
            if method.upper() in ["POST", "PUT", "PATCH"]:
                response = self.sync_client.request(
                    method, url, json=data, params=params
                )
            else:
                response = self.sync_client.request(
                    method, url, params=params
                )
            logger.info(f"Jira response status code: {response.status_code}")
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return {"status": response.status_code, "message": "Success"}
        except httpx.HTTPStatusError as e:
            logger.error(f"Jira request to {url} failed: {str(e)}")
            error_details = ""
            try:
                res_json = e.response.json()
                error_details = f" | Details: {json.dumps(res_json)}"
            except ValueError:
                error_details = f" | Details: {e.response.text}"
            return {"success": False, "error": f"Request failed: {str(e)}{error_details}"}
        except httpx.RequestError as e:
            logger.error(f"Jira network request to {url} failed: {str(e)}")
            return {"success": False, "error": f"Network request failed: {str(e)}"}

    async def make_async_request(
        self, method: str, path: str, data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.jira_url}/{path.lstrip('/')}"
        logger.info(f"Making async Jira {method} request to: {url} (reusing connection pool)")
        try:
            if method.upper() in ["POST", "PUT", "PATCH"]:
                response = await self.async_client.request(
                    method, url, json=data, params=params
                )
            else:
                response = await self.async_client.request(
                    method, url, params=params
                )
            logger.info(f"Async Jira response status code: {response.status_code}")
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return {"status": response.status_code, "message": "Success"}
        except httpx.HTTPStatusError as e:
            logger.error(f"Async Jira request to {url} failed: {str(e)}")
            error_details = ""
            try:
                res_json = e.response.json()
                error_details = f" | Details: {json.dumps(res_json)}"
            except ValueError:
                error_details = f" | Details: {e.response.text}"
            return {"success": False, "error": f"Request failed: {str(e)}{error_details}"}
        except httpx.RequestError as e:
            logger.error(f"Async Jira network request to {url} failed: {str(e)}")
            return {"success": False, "error": f"Network request failed: {str(e)}"}
        except Exception as e:
            logger.error(f"Async Jira request to {url} failed with error: {str(e)}")
            return {"success": False, "error": f"Unexpected error: {str(e)}"}


# --- Input Schemas ---

class JiraGetMyselfInput(ToolInputSchema):
    pass


class JiraCreateIssueInput(ToolInputSchema):
    project_key: str = Field(description="The key of the Jira project, e.g., 'PROJ'.")
    summary: str = Field(description="The summary or title of the issue.")
    description: str = Field(default="", description="The detailed description of the issue.")
    issue_type: str = Field(default="Task", description="The issue type (e.g., 'Task', 'Bug', 'Story', 'Epic').")
    labels: Optional[List[str]] = Field(default=None, description="Optional list of labels to add to the issue.")
    priority: Optional[str] = Field(default=None, description="Optional priority name (e.g., 'Highest', 'High', 'Medium', 'Low', 'Lowest').")


class JiraSearchIssuesInput(ToolInputSchema):
    jql: str = Field(description="The JQL (Jira Query Language) query string. IMPORTANT: Unbounded queries are not allowed on this Jira instance. You MUST specify a project or other boundary in your JQL, e.g., 'project = MDP ORDER BY created DESC'.")
    max_results: int = Field(default=50, description="The maximum number of issues to return (default: 50, max: 100).")
    fields: List[str] = Field(
        default=["key", "summary", "status", "issuetype", "assignee", "updated"],
        description="The fields to retrieve for each issue."
    )


class JiraGetIssueInput(ToolInputSchema):
    issue_key: str = Field(description="The key of the Jira issue, e.g., 'PROJ-123'.")


class JiraAddCommentInput(ToolInputSchema):
    issue_key: str = Field(description="The key of the Jira issue, e.g., 'PROJ-123'.")
    body: str = Field(description="The text body of the comment.")


class JiraTransitionIssueInput(ToolInputSchema):
    issue_key: str = Field(description="The key of the Jira issue, e.g., 'PROJ-123'.")
    transition: str = Field(description="The name or ID of the transition (e.g., 'In Progress', 'Done', or '21').")


# --- Tool Definitions ---

class JiraGetMyselfTool(AbstractTool):
    name: str = "jira_get_myself"
    description: str = "Retrieve details about the currently authenticated Jira user."
    args_schema: Type[BaseModel] = JiraGetMyselfInput
    jira_client: JiraAPIClient

    def _run(self, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        res = self.jira_client.make_request("GET", "/rest/api/2/myself")
        return json.dumps(res)

    async def _arun(self, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        res = await self.jira_client.make_async_request("GET", "/rest/api/2/myself")
        return json.dumps(res)


class JiraCreateIssueTool(AbstractTool):
    name: str = "jira_create_issue"
    description: str = "Creates a new issue (Task, Bug, Story, Epic) in a specified Jira project."
    args_schema: Type[BaseModel] = JiraCreateIssueInput
    jira_client: JiraAPIClient

    def _run(
        self,
        project_key: str,
        summary: str,
        description: str = "",
        issue_type: str = "Task",
        labels: Optional[List[str]] = None,
        priority: Optional[str] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type}
        }
        if labels:
            fields["labels"] = labels
        if priority:
            fields["priority"] = {"name": priority}
        payload = {"fields": fields}

        res = self.jira_client.make_request("POST", "/rest/api/2/issue", data=payload)
        if "error" in res:
            return json.dumps({"success": False, "error": res["error"]})
        return json.dumps({
            "success": True,
            "key": res.get("key"),
            "id": res.get("id"),
            "self": res.get("self")
        })

    async def _arun(
        self,
        project_key: str,
        summary: str,
        description: str = "",
        issue_type: str = "Task",
        labels: Optional[List[str]] = None,
        priority: Optional[str] = None,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type}
        }
        if labels:
            fields["labels"] = labels
        if priority:
            fields["priority"] = {"name": priority}
        payload = {"fields": fields}

        res = await self.jira_client.make_async_request("POST", "/rest/api/2/issue", data=payload)
        if isinstance(res, dict) and "error" in res:
            return json.dumps({"success": False, "error": res["error"]})
        return json.dumps({
            "success": True,
            "key": res.get("key"),
            "id": res.get("id"),
            "self": res.get("self")
        })


class JiraSearchIssuesTool(AbstractTool):
    name: str = "jira_search_issues"
    description: str = "Searches for Jira issues using a JQL query string."
    args_schema: Type[BaseModel] = JiraSearchIssuesInput
    jira_client: JiraAPIClient

    def _run(
        self,
        jql: str,
        max_results: int = 50,
        fields: List[str] = ["key", "summary", "status", "issuetype", "assignee", "updated"],
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        payload = {
            "jql": jql,
            "maxResults": min(max_results, 100),
            "fields": fields
        }
        res = self.jira_client.make_request("POST", "/rest/api/3/search/jql", data=payload)
        if "error" in res:
            return json.dumps({"success": False, "error": res["error"]})
        
        issues = res.get("issues", [])
        cleaned_issues = []
        for issue in issues:
            cleaned_issue = {
                "key": issue.get("key"),
                "id": issue.get("id"),
                "self": issue.get("self"),
            }
            issue_fields = issue.get("fields", {})
            for f in fields:
                if f in ["key", "id", "self"]:
                    continue
                val = issue_fields.get(f)
                if isinstance(val, dict):
                    if "name" in val:
                        cleaned_issue[f] = val["name"]
                    elif "displayName" in val:
                        cleaned_issue[f] = val["displayName"]
                    else:
                        cleaned_issue[f] = val
                else:
                    cleaned_issue[f] = val
            cleaned_issues.append(cleaned_issue)

        return json.dumps({
            "success": True,
            "isLast": res.get("isLast"),
            "nextPageToken": res.get("nextPageToken"),
            "issues": cleaned_issues
        })

    async def _arun(
        self,
        jql: str,
        max_results: int = 50,
        fields: List[str] = ["key", "summary", "status", "issuetype", "assignee", "updated"],
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        payload = {
            "jql": jql,
            "maxResults": min(max_results, 100),
            "fields": fields
        }
        res = await self.jira_client.make_async_request("POST", "/rest/api/3/search/jql", data=payload)
        if isinstance(res, dict) and "error" in res:
            return json.dumps({"success": False, "error": res["error"]})
        
        issues = res.get("issues", [])
        cleaned_issues = []
        for issue in issues:
            cleaned_issue = {
                "key": issue.get("key"),
                "id": issue.get("id"),
                "self": issue.get("self"),
            }
            issue_fields = issue.get("fields", {})
            for f in fields:
                if f in ["key", "id", "self"]:
                    continue
                val = issue_fields.get(f)
                if isinstance(val, dict):
                    if "name" in val:
                        cleaned_issue[f] = val["name"]
                    elif "displayName" in val:
                        cleaned_issue[f] = val["displayName"]
                    else:
                        cleaned_issue[f] = val
                else:
                    cleaned_issue[f] = val
            cleaned_issues.append(cleaned_issue)

        return json.dumps({
            "success": True,
            "isLast": res.get("isLast"),
            "nextPageToken": res.get("nextPageToken"),
            "issues": cleaned_issues
        })


class JiraGetIssueTool(AbstractTool):
    name: str = "jira_get_issue"
    description: str = "Retrieves full details of a specific Jira issue by its key."
    args_schema: Type[BaseModel] = JiraGetIssueInput
    jira_client: JiraAPIClient

    def _run(self, issue_key: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        path = f"/rest/api/2/issue/{issue_key}"
        res = self.jira_client.make_request("GET", path)
        if "error" in res:
            return json.dumps({"success": False, "error": res["error"]})
        return json.dumps({"success": True, "issue": res})

    async def _arun(self, issue_key: str, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        path = f"/rest/api/2/issue/{issue_key}"
        res = await self.jira_client.make_async_request("GET", path)
        if isinstance(res, dict) and "error" in res:
            return json.dumps({"success": False, "error": res["error"]})
        return json.dumps({"success": True, "issue": res})


class JiraAddCommentTool(AbstractTool):
    name: str = "jira_add_comment"
    description: str = "Adds a new comment to a specified Jira issue."
    args_schema: Type[BaseModel] = JiraAddCommentInput
    jira_client: JiraAPIClient

    def _run(self, issue_key: str, body: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        path = f"/rest/api/2/issue/{issue_key}/comment"
        payload = {"body": body}
        res = self.jira_client.make_request("POST", path, data=payload)
        if "error" in res:
            return json.dumps({"success": False, "error": res["error"]})
        return json.dumps({"success": True, "comment_id": res.get("id"), "self": res.get("self")})

    async def _arun(self, issue_key: str, body: str, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        path = f"/rest/api/2/issue/{issue_key}/comment"
        payload = {"body": body}
        res = await self.jira_client.make_async_request("POST", path, data=payload)
        if isinstance(res, dict) and "error" in res:
            return json.dumps({"success": False, "error": res["error"]})
        return json.dumps({"success": True, "comment_id": res.get("id"), "self": res.get("self")})


class JiraTransitionIssueTool(AbstractTool):
    name: str = "jira_transition_issue"
    description: str = "Transitions an issue to a new status (e.g., 'In Progress', 'Done') by name or ID."
    args_schema: Type[BaseModel] = JiraTransitionIssueInput
    jira_client: JiraAPIClient

    def _run(self, issue_key: str, transition: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        path = f"/rest/api/2/issue/{issue_key}/transitions"
        res = self.jira_client.make_request("GET", path)
        if "error" in res:
            return json.dumps({"success": False, "error": res["error"]})

        transitions_list = res.get("transitions", [])
        transition_id = None
        
        if transition.isdigit():
            transition_id = transition
        else:
            for t in transitions_list:
                if t.get("name", "").lower() == transition.lower():
                    transition_id = t.get("id")
                    break
        
        if not transition_id:
            valid_names = [t.get("name") for t in transitions_list if t.get("name")]
            valid_ids = [t.get("id") for t in transitions_list if t.get("id")]
            return json.dumps({
                "success": False, 
                "error": f"Transition '{transition}' not found for issue {issue_key}.",
                "available_transitions": valid_names,
                "available_ids": valid_ids
            })

        payload = {"transition": {"id": transition_id}}
        post_res = self.jira_client.make_request("POST", path, data=payload)
        if "error" in post_res:
            return json.dumps({"success": False, "error": post_res["error"]})
        
        return json.dumps({"success": True, "message": f"Issue {issue_key} transitioned successfully."})

    async def _arun(self, issue_key: str, transition: str, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        path = f"/rest/api/2/issue/{issue_key}/transitions"
        res = await self.jira_client.make_async_request("GET", path)
        if isinstance(res, dict) and "error" in res:
            return json.dumps({"success": False, "error": res["error"]})

        transitions_list = res.get("transitions", [])
        transition_id = None
        
        if transition.isdigit():
            transition_id = transition
        else:
            for t in transitions_list:
                if t.get("name", "").lower() == transition.lower():
                    transition_id = t.get("id")
                    break
        
        if not transition_id:
            valid_names = [t.get("name") for t in transitions_list if t.get("name")]
            valid_ids = [t.get("id") for t in transitions_list if t.get("id")]
            return json.dumps({
                "success": False, 
                "error": f"Transition '{transition}' not found for issue {issue_key}.",
                "available_transitions": valid_names,
                "available_ids": valid_ids
            })

        payload = {"transition": {"id": transition_id}}
        post_res = await self.jira_client.make_async_request("POST", path, data=payload)
        if isinstance(post_res, dict) and "error" in post_res:
            return json.dumps({"success": False, "error": post_res["error"]})
        
        return json.dumps({"success": True, "message": f"Issue {issue_key} transitioned successfully."})


__all__ = [
    "JiraAPIClient",
    "JiraGetMyselfTool",
    "JiraCreateIssueTool",
    "JiraSearchIssuesTool",
    "JiraGetIssueTool",
    "JiraAddCommentTool",
    "JiraTransitionIssueTool",
]

if __name__ == "__main__":
    jira_client = JiraAPIClient()
    jira_myself_tool = JiraGetMyselfTool(jira_client=jira_client)
    print(jira_myself_tool.invoke(""))

    jira_search_issues_tool = JiraSearchIssuesTool(jira_client=jira_client)
    print(jira_search_issues_tool.run("JQL: project = 'ERP' AND status = 'Open'"))