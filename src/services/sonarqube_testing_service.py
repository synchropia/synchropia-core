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
    """A comprehensive client for interacting with the SonarQube / SonarCloud REST API."""

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None):
        self.url = url or settings.SONARQUBE_BASE_URL
        self.token = token or settings.SONARQUBE_API_KEY

        if self.url:
            self.url = self.url.strip('"\' ').rstrip("/")
        if self.token:
            self.token = self.token.strip('"\' ')

        if not self.url or not self.token:
            raise ValueError("SonarQube URL and Token must be configured in environment variables or passed directly.")

        self.auth = httpx.BasicAuth(username=self.token, password="")
        
        self.sync_client = httpx.Client(auth=self.auth, timeout=30.0)
        self.async_client = httpx.AsyncClient(auth=self.auth, timeout=30.0)
        logger.success("SonarQube API client initialized successfully.")

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
            try:
                return response.json()
            except ValueError:
                return {"status": response.status_code, "message": "Success"}
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
            try:
                return response.json()
            except ValueError:
                return {"status": response.status_code, "message": "Success"}
        except httpx.HTTPStatusError as e:
            logger.error(f"Async SonarQube request failed: {e.response.text}")
            return {"success": False, "error": f"HTTP status error: {str(e)}", "details": e.response.text}
        except httpx.RequestError as e:
            logger.error(f"Async SonarQube network request failed: {str(e)}")
            return {"success": False, "error": f"Network error: {str(e)}"}


# --- Input Schemas ---

class GetProjectStatusInput(ToolInputSchema):
    project_key: str = Field(description="The unique key of the SonarQube project (e.g., 'org_project-name').")


# --- Tool Definitions ---
class SonarQubeGetProjectStatusTool(AbstractTool):
    """Retrieves the Quality Gate status of a target project."""
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

class ProvisionGitHubProjectInput(ToolInputSchema):
    organization: str = Field(default="synchropia", description="The target SonarCloud organization key.")
    installation_keys: str = Field(description="The numerical GitHub repository ID to provision.")

class ListGithubReposInput(ToolInputSchema):
    organization: str = Field(description="The GitHub organization/user account name in SonarCloud (e.g. 'synchropia').")
    alm_setting: str = Field(default="github", description="The DevOps platform setting key.")

sonarqube_client = SonarQubeAPIClient()

__all__ = [
    "sonarqube_client",
    "SonarQubeGetProjectStatusTool"
]

if __name__ == "__main__":
    client = SonarQubeAPIClient()
        
        # 1. Discover repositories to find their numerical IDs
    tool = SonarQubeListGithubReposTool(sonarqube_client=client)
    
