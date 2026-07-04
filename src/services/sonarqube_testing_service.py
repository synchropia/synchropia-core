# C:\Users\Raghu\Downloads\MAS_Agents\synchropia\src\services\sonarqube_service.py
import json
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Type
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
    """Base schema for SonarQube tools."""
    pass

class AbstractTool(BaseTool, ABC):
    name: str
    description: str
    args_schema: Optional[Type[BaseModel]] = None
    return_direct: bool = False

    @abstractmethod
    def _run(
        self, *args, run_manager: Optional[CallbackManagerForToolRun] = None, **kwargs
    ):
        """Sync execution logic."""

    @abstractmethod
    async def _arun(
        self,
        *args,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
        **kwargs,
    ):
        """Async execution logic."""


class SonarQubeAPIClient:
    """A comprehensive client for interacting with the SonarQube / SonarCloud REST API.
    
    Uses basic authentication where the username is the User Token and the password 
    remains empty, managed through persistent connection pools.
    """

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None):
        self.url = url or settings.SONARQUBE_BASE_URL
        self.token = token or settings.SONARQUBE_API_KEY

        if self.url:
            self.url = self.url.strip('"\' ').rstrip("/")
        if self.token:
            self.token = self.token.strip('"\' ')

        if not self.url or not self.token:
            raise ValueError("SonarQube URL and Token must be configured in environment variables or passed directly.")

        # SonarQube authenticates using the token as the username with an empty password
        self.auth = httpx.BasicAuth(username=self.token, password="")
        
        # Setup persistent clients
        self.sync_client = httpx.Client(auth=self.auth, timeout=30.0)
        self.async_client = httpx.AsyncClient(auth=self.auth, timeout=30.0)
        logger.success("SonarQube API client initialized successfully with persistent TCP connection pools.")

    def __del__(self):
        try:
            self.sync_client.close()
        except Exception:
            pass

    def make_request(
        self, method: str, path: str, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.url}/{path.lstrip('/')}"
        logger.info(f"Making SonarQube {method} request to: {url}")
        try:
            response = self.sync_client.request(method, url, params=params, json=data)
            logger.info(f"SonarQube response status code: {response.status_code}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"SonarQube request failed: {e.response.text}")
            return {"success": False, "error": f"HTTP status error: {str(e)}", "details": e.response.text}
        except httpx.RequestError as e:
            logger.error(f"SonarQube network request failed: {str(e)}")
            return {"success": False, "error": f"Network error: {str(e)}"}

    async def make_async_request(
        self, method: str, path: str, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.url}/{path.lstrip('/')}"
        logger.info(f"Making async SonarQube {method} request to: {url}")
        try:
            response = await self.async_client.request(method, url, params=params, json=data)
            logger.info(f"Async SonarQube response status code: {response.status_code}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Async SonarQube request failed: {e.response.text}")
            return {"success": False, "error": f"HTTP status error: {str(e)}", "details": e.response.text}
        except httpx.RequestError as e:
            logger.error(f"Async SonarQube network request failed: {str(e)}")
            return {"success": False, "error": f"Network error: {str(e)}"}


# --- Input Schemas ---

class GetProjectStatusInput(ToolInputSchema):
    project_key: str = Field(description="The unique key of the SonarQube project (e.g., 'org_project-name').")


class GetProjectMeasuresInput(ToolInputSchema):
    project_key: str = Field(description="The unique key of the SonarQube project.")
    metric_keys: str = Field(
        default="bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density",
        description="Comma-separated list of metrics to extract from the component."
    )


# --- Tool Definitions ---

class SonarQubeGetProjectStatusTool(AbstractTool):
    """Retrieves the Quality Gate status (PASSED or FAILED) of a target project."""
    name: str = "sonarqube_get_project_status"
    description: str = "Retrieves the Quality Gate status (green/red) for a target project using its unique project key."
    args_schema: Type[BaseModel] = GetProjectStatusInput
    sonarqube_client: SonarQubeAPIClient

    def _run(self, project_key: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        params = {"projectKey": project_key}
        res = self.sonarqube_client.make_request("GET", "api/qualitygates/project_status", params=params)
        return json.dumps(res)

    async def _arun(self, project_key: str, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        params = {"projectKey": project_key}
        res = await self.sonarqube_client.make_async_request("GET", "api/qualitygates/project_status", params=params)
        return json.dumps(res)


class SonarQubeGetProjectMeasuresTool(AbstractTool):
    """Extracts specific code quality measures (coverage, bugs, code smells) for a project."""
    name: str = "sonarqube_get_project_measures"
    description: str = "Fetches code metrics (bugs, vulnerabilities, code smells, unit test coverage) of a project component."
    args_schema: Type[BaseModel] = GetProjectMeasuresInput
    sonarqube_client: SonarQubeAPIClient

    def _run(self, project_key: str, metric_keys: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        params = {
            "component": project_key,
            "metricKeys": metric_keys
        }
        res = self.sonarqube_client.make_request("GET", "api/measures/component", params=params)
        return json.dumps(res)

    async def _arun(self, project_key: str, metric_keys: str, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        params = {
            "component": project_key,
            "metricKeys": metric_keys
        }
        res = await self.sonarqube_client.make_async_request("GET", "api/measures/component", params=params)
        return json.dumps(res)


class GetProjectIssuesInput(ToolInputSchema):
    project_key: str = Field(description="The unique key of the SonarQube project.")
    severities: str = Field(
        default="",
        description="Optional comma-separated list of severities (e.g. 'INFO,MINOR,MAJOR,CRITICAL,BLOCKER')."
    )
    types: str = Field(
        default="",
        description="Optional comma-separated list of types (e.g. 'BUG,VULNERABILITY,CODE_SMELL')."
    )
    resolved: bool = Field(
        default=False,
        description="If false, only unresolved issues are returned. If true, both resolved and unresolved are returned."
    )

class SonarQubeGetProjectMeasuresTool(AbstractTool):
    """Extracts specific code quality measures (coverage, bugs, code smells) for a project."""
    name: str = "sonarqube_get_project_measures"
    description: str = "Fetches code metrics (bugs, vulnerabilities, code smells, unit test coverage) of a project component."
    args_schema: Type[BaseModel] = GetProjectMeasuresInput
    sonarqube_client: SonarQubeAPIClient

    def _run(self, project_key: str, metric_keys: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        params = {
            "component": project_key,
            "metricKeys": metric_keys
        }
        res = self.sonarqube_client.make_request("GET", "api/measures/component", params=params)
        return json.dumps(res)

    async def _arun(self, project_key: str, metric_keys: str, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        params = {
            "component": project_key,
            "metricKeys": metric_keys
        }
        res = await self.sonarqube_client.make_async_request("GET", "api/measures/component", params=params)
        return json.dumps(res)

class SonarQubeGetProjectIssuesTool(AbstractTool):
    """Retrieves list of code quality issues (bugs, vulnerabilities, smells) for a project."""
    name: str = "sonarqube_get_project_issues"
    description: str = "Fetches code quality issues (bugs, vulnerabilities, code smells) for a project with options to filter by type and severity."
    args_schema: Type[BaseModel] = GetProjectIssuesInput
    sonarqube_client: SonarQubeAPIClient

    def _run(
        self,
        project_key: str,
        severities: str = "",
        types: str = "",
        resolved: bool = False,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params = {
            "componentKeys": project_key,
            "resolved": "true" if resolved else "false"
        }
        if severities:
            params["severities"] = severities
        if types:
            params["types"] = types
        res = self.sonarqube_client.make_request("GET", "api/issues/search", params=params)
        return json.dumps(res)

    async def _arun(
        self,
        project_key: str,
        severities: str = "",
        types: str = "",
        resolved: bool = False,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        params = {
            "componentKeys": project_key,
            "resolved": "true" if resolved else "false"
        }
        if severities:
            params["severities"] = severities
        if types:
            params["types"] = types
        res = await self.sonarqube_client.make_async_request("GET", "api/issues/search", params=params)
        return json.dumps(res)


sonarqube_client = SonarQubeAPIClient()

__all__ = [
    "sonarqube_client",
    "SonarQubeGetProjectStatusTool",
    "SonarQubeGetProjectMeasuresTool",
    "SonarQubeGetProjectIssuesTool",
    "SonarQubeImportGitHubProjectTool"
]


if __name__ == "__main__":
    client = SonarQubeAPIClient()
    import_projects_tool = SonarQubeImportGitHubProjectTool(sonarqube_client=client)
    try:
        res = import_projects_tool.invoke({
                 "organization": "sonarqube" ,
        "github_repo_id":"1282314054",
        "project_key":"synchropia",
       "project_name":"synchropia/Synchropia-web", 
            }) 
        print(res)
        
    except Exception as e:
        logger.error(f"error importing projects into the sonarqube repo: {e}")