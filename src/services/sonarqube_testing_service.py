"""
src/services/sonarqube_testing_service.py
-----------------------------------------
SonarQube / SonarCloud REST API client and LangChain-compatible tools
used by the QA Agent to fetch code quality data, quality gate status,
issues, and component measures.
"""

import json
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Type

import httpx
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, Field
import sys
from pathlib import Path
# Add project root to sys.path to support running directly or debugging
sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.config.settings import settings
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool

load_dotenv()


# ---------------------------------------------------------------------------
# Base abstractions
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class SonarQubeAPIClient:
    """A comprehensive client for interacting with the SonarQube / SonarCloud REST API.
    
    Uses basic authentication where the username is the User Token and the password 
    remains empty, managed through persistent connection pools.
    """

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None):
        self.url = url or settings.SONARQUBE_BASE_URL
        self.token = token or settings.SONARQUBE_API_KEY

        if self.url:
            self.url = self.url.strip('"\'  ').rstrip("/")
        if self.token:
            self.token = self.token.strip('"\'  ')

        if not self.url or not self.token:
            raise ValueError(
                "SonarQube URL and Token must be configured in environment variables or passed directly."
            )

        self.auth = httpx.BasicAuth(username=self.token, password="")
        self.sync_client = httpx.Client(auth=self.auth, timeout=30.0)
        self.async_client = httpx.AsyncClient(auth=self.auth, timeout=30.0)
        logger.success("SonarQube API client initialized successfully.")

    def close(self):
        """Gracefully close the HTTP client sessions."""
        try:
            self.sync_client.close()
        except Exception:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def make_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
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
            return {
                "success": False,
                "error": f"HTTP status error: {str(e)}",
                "details": e.response.text,
            }
        except httpx.RequestError as e:
            logger.error(f"SonarQube network request failed: {str(e)}")
            return {"success": False, "error": f"Network error: {str(e)}"}

    async def make_async_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.url}/{path.lstrip('/')}"
        logger.info(f"Making async SonarQube {method} request to: {url}")
        try:
            response = await self.async_client.request(
                method, url, params=params, json=data
            )
            logger.info(f"Async SonarQube response status code: {response.status_code}")
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return {"status": response.status_code, "message": "Success"}
        except httpx.HTTPStatusError as e:
            logger.error(f"Async SonarQube request failed: {e.response.text}")
            return {
                "success": False,
                "error": f"HTTP status error: {str(e)}",
                "details": e.response.text,
            }
        except httpx.RequestError as e:
            logger.error(f"Async SonarQube network request failed: {str(e)}")
            return {"success": False, "error": f"Network error: {str(e)}"}


# ---------------------------------------------------------------------------
# Input Schemas
# ---------------------------------------------------------------------------

class GetProjectStatusInput(ToolInputSchema):
    project_key: str = Field(
        description="The unique key of the SonarQube project (e.g., 'org_project-name')."
    )


class GetProjectMeasuresInput(ToolInputSchema):
    project_key: str = Field(
        description="The unique key of the SonarQube project."
    )
    metric_keys: str = Field(
        default=(
            "bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,"
            "ncloc,security_hotspots,reliability_rating,security_rating,sqale_rating,"
            "complexity,cognitive_complexity,sqale_debt_ratio,sqale_index,violations"
        ),
        description=(
            "Comma-separated list of metric keys to retrieve. "
            "Common values: bugs, vulnerabilities, code_smells, coverage, "
            "duplicated_lines_density, ncloc, security_hotspots, reliability_rating, "
            "security_rating, sqale_rating, complexity, cognitive_complexity, "
            "sqale_debt_ratio (technical debt ratio %), sqale_index (technical debt in minutes), "
            "violations (total rule violations)."
        ),
    )


class GetMetricHistoryInput(ToolInputSchema):
    project_key: str = Field(
        description="The unique key of the SonarQube project."
    )
    metrics: str = Field(
        default="bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density",
        description="Comma-separated metric keys whose history to retrieve.",
    )
    page_size: int = Field(
        default=10,
        description="Number of historical analysis snapshots to retrieve (max 1000).",
    )


class ListProjectIssuesInput(ToolInputSchema):
    project_key: str = Field(
        description="The SonarQube project key to list issues for."
    )
    types: str = Field(
        default="BUG,VULNERABILITY,CODE_SMELL",
        description="Comma-separated issue types: BUG, VULNERABILITY, CODE_SMELL, SECURITY_HOTSPOT.",
    )
    severities: Optional[str] = Field(
        default=None,
        description="Comma-separated severities: INFO, MINOR, MAJOR, CRITICAL, BLOCKER. Leave empty for all.",
    )
    resolved: Optional[bool] = Field(
        default=False,
        description="If True, return only resolved issues. If False, return only open issues.",
    )
    page_size: int = Field(
        default=50,
        description="Number of issues to return per page (max 500).",
    )


class SearchProjectsInput(ToolInputSchema):
    organization: str = Field(
        description="The SonarCloud organization key (e.g., 'synchropia')."
    )
    query: Optional[str] = Field(
        default=None,
        description="Optional search string to filter projects by name.",
    )
    page_size: int = Field(
        default=20,
        description="Number of projects to return (max 500).",
    )


class ListGithubReposInput(ToolInputSchema):
    organization: str = Field(
        description="The GitHub organization/user account name in SonarCloud (e.g. 'synchropia')."
    )
    alm_setting: str = Field(
        default="github",
        description="The DevOps platform setting key.",
    )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

class SonarQubeGetProjectStatusTool(AbstractTool):
    """Retrieves the Quality Gate status of a target project."""

    name: str = "sonarqube_get_project_status"
    description: str = (
        "Retrieves the Quality Gate status (PASSED / FAILED) for a SonarQube project "
        "using its unique project key. Use this to verify whether a project meets all "
        "configured quality conditions before merging or deploying."
    )
    args_schema: Type[BaseModel] = GetProjectStatusInput
    sonarqube_client: SonarQubeAPIClient

    def _run(
        self,
        project_key: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params = {"projectKey": project_key}
        res = self.sonarqube_client.make_request(
            "GET", "api/qualitygates/project_status", params=params
        )
        return json.dumps(res, indent=2)

    async def _arun(
        self,
        project_key: str,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        params = {"projectKey": project_key}
        res = await self.sonarqube_client.make_async_request(
            "GET", "api/qualitygates/project_status", params=params
        )
        return json.dumps(res, indent=2)


class SonarQubeGetProjectMeasuresTool(AbstractTool):
    """Retrieves detailed code quality metrics/measures for a project."""

    name: str = "sonarqube_get_project_measures"
    description: str = (
        "Retrieves detailed code quality metrics for a SonarQube project — including "
        "bug count, vulnerability count, code smell count, test coverage percentage, "
        "duplicated lines density, lines of code, security hotspots, and rating scores. "
        "Use this to produce a full code quality report."
    )
    args_schema: Type[BaseModel] = GetProjectMeasuresInput
    sonarqube_client: SonarQubeAPIClient

    def _run(
        self,
        project_key: str,
        metric_keys: str = "bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc,security_hotspots,reliability_rating,security_rating,sqale_rating",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params = {"component": project_key, "metricKeys": metric_keys}
        res = self.sonarqube_client.make_request(
            "GET", "api/measures/component", params=params
        )
        # Parse into a more readable summary dict
        if "component" in res and "measures" in res["component"]:
            measures = {
                m["metric"]: m.get("value", m.get("periods", "N/A"))
                for m in res["component"]["measures"]
            }
            return json.dumps(
                {
                    "project_key": project_key,
                    "measures": measures,
                    "raw": res,
                },
                indent=2,
            )
        return json.dumps(res, indent=2)

    async def _arun(
        self,
        project_key: str,
        metric_keys: str = "bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc,security_hotspots,reliability_rating,security_rating,sqale_rating",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        params = {"component": project_key, "metricKeys": metric_keys}
        res = await self.sonarqube_client.make_async_request(
            "GET", "api/measures/component", params=params
        )
        if "component" in res and "measures" in res["component"]:
            measures = {
                m["metric"]: m.get("value", m.get("periods", "N/A"))
                for m in res["component"]["measures"]
            }
            return json.dumps(
                {
                    "project_key": project_key,
                    "measures": measures,
                    "raw": res,
                },
                indent=2,
            )
        return json.dumps(res, indent=2)


class SonarQubeListIssesTool(AbstractTool):
    """Lists open bugs, vulnerabilities, or code smells from a SonarQube project."""

    name: str = "sonarqube_list_issues"
    description: str = (
        "Lists open issues (bugs, vulnerabilities, code smells) for a SonarQube project. "
        "Use this to identify specific code problems that need to be fixed. "
        "Returns issue key, message, severity, component, and line number."
    )
    args_schema: Type[BaseModel] = ListProjectIssuesInput
    sonarqube_client: SonarQubeAPIClient

    def _run(
        self,
        project_key: str,
        types: str = "BUG,VULNERABILITY,CODE_SMELL",
        severities: Optional[str] = None,
        resolved: Optional[bool] = False,
        page_size: int = 50,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params: Dict[str, Any] = {
            "componentKeys": project_key,
            "types": types,
            "ps": min(page_size, 500),
            "resolved": str(resolved).lower(),
        }
        if severities:
            params["severities"] = severities

        res = self.sonarqube_client.make_request("GET", "api/issues/search", params=params)

        if "issues" in res:
            simplified = [
                {
                    "key": i.get("key"),
                    "type": i.get("type"),
                    "severity": i.get("severity"),
                    "message": i.get("message"),
                    "component": i.get("component"),
                    "line": i.get("line"),
                    "status": i.get("status"),
                    "effort": i.get("effort"),
                }
                for i in res["issues"]
            ]
            return json.dumps(
                {
                    "project_key": project_key,
                    "total": res.get("total", len(simplified)),
                    "issues": simplified,
                },
                indent=2,
            )
        return json.dumps(res, indent=2)

    async def _arun(
        self,
        project_key: str,
        types: str = "BUG,VULNERABILITY,CODE_SMELL",
        severities: Optional[str] = None,
        resolved: Optional[bool] = False,
        page_size: int = 50,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        params: Dict[str, Any] = {
            "componentKeys": project_key,
            "types": types,
            "ps": min(page_size, 500),
            "resolved": str(resolved).lower(),
        }
        if severities:
            params["severities"] = severities

        res = await self.sonarqube_client.make_async_request(
            "GET", "api/issues/search", params=params
        )

        if "issues" in res:
            simplified = [
                {
                    "key": i.get("key"),
                    "type": i.get("type"),
                    "severity": i.get("severity"),
                    "message": i.get("message"),
                    "component": i.get("component"),
                    "line": i.get("line"),
                    "status": i.get("status"),
                    "effort": i.get("effort"),
                }
                for i in res["issues"]
            ]
            return json.dumps(
                {
                    "project_key": project_key,
                    "total": res.get("total", len(simplified)),
                    "issues": simplified,
                },
                indent=2,
            )
        return json.dumps(res, indent=2)


class SonarQubeSearchProjectsTool(AbstractTool):
    """Searches for SonarCloud projects within an organization."""

    name: str = "sonarqube_search_projects"
    description: str = (
        "Searches for all SonarCloud projects within an organization. "
        "Returns project keys, names, and last analysis dates. "
        "Use this to discover what projects are analysed on SonarCloud for a given org."
    )
    args_schema: Type[BaseModel] = SearchProjectsInput
    sonarqube_client: SonarQubeAPIClient

    def _run(
        self,
        organization: str,
        query: Optional[str] = None,
        page_size: int = 20,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params: Dict[str, Any] = {"organization": organization, "ps": min(page_size, 500)}
        if query:
            params["q"] = query

        res = self.sonarqube_client.make_request(
            "GET", "api/components/search_projects", params=params
        )

        if "components" in res:
            simplified = [
                {
                    "key": c.get("key"),
                    "name": c.get("name"),
                    "lastAnalysisDate": c.get("lastAnalysisDate"),
                    "qualifier": c.get("qualifier"),
                }
                for c in res["components"]
            ]
            return json.dumps(
                {
                    "organization": organization,
                    "total": res.get("paging", {}).get("total", len(simplified)),
                    "projects": simplified,
                },
                indent=2,
            )
        return json.dumps(res, indent=2)

    async def _arun(
        self,
        organization: str,
        query: Optional[str] = None,
        page_size: int = 20,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        params: Dict[str, Any] = {"organization": organization, "ps": min(page_size, 500)}
        if query:
            params["q"] = query

        res = await self.sonarqube_client.make_async_request(
            "GET", "api/components/search_projects", params=params
        )

        if "components" in res:
            simplified = [
                {
                    "key": c.get("key"),
                    "name": c.get("name"),
                    "lastAnalysisDate": c.get("lastAnalysisDate"),
                    "qualifier": c.get("qualifier"),
                }
                for c in res["components"]
            ]
            return json.dumps(
                {
                    "organization": organization,
                    "total": res.get("paging", {}).get("total", len(simplified)),
                    "projects": simplified,
                },
                indent=2,
            )
        return json.dumps(res, indent=2)


class SonarQubeGetMetricHistoryTool(AbstractTool):
    """Retrieves historical trend data for a project's metrics over time."""

    name: str = "sonarqube_get_metric_history"
    description: str = (
        "Retrieves historical trend data for a SonarQube project's key metrics over time. "
        "Use this to detect whether code quality is improving or degrading sprint-over-sprint. "
        "Returns metric values paired with their analysis dates."
    )
    args_schema: Type[BaseModel] = GetMetricHistoryInput
    sonarqube_client: SonarQubeAPIClient

    def _run(
        self,
        project_key: str,
        metrics: str = "bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density",
        page_size: int = 10,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params: Dict[str, Any] = {
            "component": project_key,
            "metrics": metrics,
            "ps": min(page_size, 1000),
        }
        res = self.sonarqube_client.make_request(
            "GET", "api/measures/search_history", params=params
        )
        if "measures" in res:
            trend = {
                m["metric"]: [
                    {"date": h.get("date"), "value": h.get("value")}
                    for h in m.get("history", [])
                ]
                for m in res["measures"]
            }
            return json.dumps(
                {"project_key": project_key, "trend": trend}, indent=2
            )
        return json.dumps(res, indent=2)

    async def _arun(
        self,
        project_key: str,
        metrics: str = "bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density",
        page_size: int = 10,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        params: Dict[str, Any] = {
            "component": project_key,
            "metrics": metrics,
            "ps": min(page_size, 1000),
        }
        res = await self.sonarqube_client.make_async_request(
            "GET", "api/measures/search_history", params=params
        )
        if "measures" in res:
            trend = {
                m["metric"]: [
                    {"date": h.get("date"), "value": h.get("value")}
                    for h in m.get("history", [])
                ]
                for m in res["measures"]
            }
            return json.dumps(
                {"project_key": project_key, "trend": trend}, indent=2
            )
        return json.dumps(res, indent=2)


class SonarQubeListGithubReposTool(AbstractTool):
    """Lists GitHub repositories for an organization in SonarCloud."""

    name: str = "sonarqube_list_github_repositories"
    description: str = (
        "Lists all GitHub repositories bound to a SonarCloud organization. "
        "Use this to find which repositories have SonarCloud analysis configured "
        "and to retrieve their numerical IDs for further operations."
    )
    args_schema: Type[BaseModel] = ListGithubReposInput
    sonarqube_client: SonarQubeAPIClient

    def _run(
        self,
        organization: str,
        alm_setting: str = "github",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params = {"organization": organization, "almSetting": alm_setting}

        primary_path = "api/alm_integrations/list_github_repositories"
        res = self.sonarqube_client.make_request("GET", primary_path, params=params)

        if isinstance(res, dict) and (
            "Unknown url" in res.get("details", "") or res.get("status") == 404
        ):
            fallback_path = "api/alm_integration/list_github_repositories"
            logger.warning(
                f"Plural endpoint returned 404. Falling back to singular: {fallback_path}"
            )
            res = self.sonarqube_client.make_request("GET", fallback_path, params=params)

        return json.dumps(res, indent=2)

    async def _arun(
        self,
        organization: str,
        alm_setting: str = "github",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        params = {"organization": organization, "almSetting": alm_setting}

        primary_path = "api/alm_integrations/list_github_repositories"
        res = await self.sonarqube_client.make_async_request(
            "GET", primary_path, params=params
        )

        if isinstance(res, dict) and (
            "Unknown url" in res.get("details", "") or res.get("status") == 404
        ):
            fallback_path = "api/alm_integration/list_github_repositories"
            logger.warning(
                f"Plural endpoint returned 404. Falling back to singular: {fallback_path}"
            )
            res = await self.sonarqube_client.make_async_request(
                "GET", fallback_path, params=params
            )

        return json.dumps(res, indent=2)


# ---------------------------------------------------------------------------
# Module-level singleton client
# ---------------------------------------------------------------------------

sonarqube_client = SonarQubeAPIClient()

__all__ = [
    "SonarQubeAPIClient",
    "sonarqube_client",
    # Tools
    "SonarQubeGetProjectStatusTool",
    "SonarQubeGetProjectMeasuresTool",
    "SonarQubeGetMetricHistoryTool",
    "SonarQubeListIssesTool",
    "SonarQubeSearchProjectsTool",
    "SonarQubeListGithubReposTool",
]


# ---------------------------------------------------------------------------
# Quick manual test (run: python -m src.services.sonarqube_testing_service)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        client = sonarqube_client

        # 1. Quality Gate status
        status_tool = SonarQubeGetProjectStatusTool(sonarqube_client=client)
        logger.info("Fetching Quality Gate status...")
        print(status_tool.invoke({"project_key": "synchropia_synchropia-core"}))

        # 2. Project measures
        measures_tool = SonarQubeGetProjectMeasuresTool(sonarqube_client=client)
        logger.info("Fetching project measures...")
        print(measures_tool.invoke({"project_key": "synchropia_synchropia-core"}))

        # 3. Issues list
        issues_tool = SonarQubeListIssesTool(sonarqube_client=client)
        logger.info("Fetching open issues...")
        print(issues_tool.invoke({"project_key": "synchropia_synchropia-core"}))

        metrics_hist_tool = SonarQubeGetMetricHistoryTool(sonarqube_client=client)
        logger.info("Fetching metric history...")
        print(metrics_hist_tool.invoke({"project_key": "synchropia_synchropia-core","metrics":"bugs,coverage", "page_size": 10}))
            
    except Exception as e:
        print(f"Test run failed: {e}")
