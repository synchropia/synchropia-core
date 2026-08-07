"""
routes/auth_route.py
--------------------
Endpoints for the web app to register/sync users and client organizations
into the backend database, so JWT-authenticated requests can be validated
against a real User row.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from src.models.db_models import Organization, User
from src.services.database import db_engine

router = APIRouter(prefix="/api/v1/auth")


class OrganizationUpsert(BaseModel):
    id: Optional[str] = None
    name: str
    github_org_name: Optional[str] = None
    web_org_id: Optional[str] = None


class UserUpsert(BaseModel):
    id: str
    name: Optional[str] = None
    email: Optional[str] = None
    role: str = "viewer"
    organization: Optional[OrganizationUpsert] = None


@router.post("/upsert", tags=["Auth"])
def upsert_user(payload: UserUpsert):
    """
    Create or update a User (and optionally its client Organization) so that
    JWT-authenticated requests from the web app can be validated locally.

    The web calls this after login / onboarding. `id` must match the JWT `sub`
    (Better Auth user id) so that get_current_user's DB lookup succeeds.
    """
    with db_engine.begin() as conn:
        org = None
        if payload.organization:
            org_query = None
            if payload.organization.id:
                org_query = conn.execute(
                    select(Organization).where(Organization.id == payload.organization.id)
                ).scalar_one_or_none()
            if org_query is None and payload.organization.web_org_id:
                org_query = conn.execute(
                    select(Organization).where(
                        Organization.web_org_id == payload.organization.web_org_id
                    )
                ).scalar_one_or_none()

            if org_query is None:
                org = Organization(
                    id=payload.organization.id or None,
                    name=payload.organization.name,
                    github_org_name=payload.organization.github_org_name
                    or payload.organization.name,
                    web_org_id=payload.organization.web_org_id,
                )
                conn.add(org)
                conn.flush()
            else:
                org_query.name = payload.organization.name
                if payload.organization.github_org_name:
                    org_query.github_org_name = payload.organization.github_org_name
                if payload.organization.web_org_id:
                    org_query.web_org_id = payload.organization.web_org_id
                org_query.updated_at = datetime.now(timezone.utc)
                org = org_query

        user = conn.execute(
            select(User).where(User.id == payload.id)
        ).scalar_one_or_none()

        if user is None:
            user = User(
                id=payload.id,
                name=payload.name,
                email=payload.email,
                role=payload.role,
                organization_id=org.id if org else None,
            )
            conn.add(user)
        else:
            if payload.name is not None:
                user.name = payload.name
            if payload.email is not None:
                user.email = payload.email
            user.role = payload.role
            user.organization_id = org.id if org else user.organization_id
            user.updated_at = datetime.now(timezone.utc)

    return {
        "ok": True,
        "user_id": payload.id,
        "organization_id": org.id if org else None,
    }
