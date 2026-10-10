"""Orgs, users and tokens for the e2e lane.

Users are written straight into Mongo (the collections the auth middleware reads) and tokens are
signed with the same secrets the API was started with, so no sign-in flow is involved.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import jwt
from bson import ObjectId
from pymongo.database import Database

from helper.collab_stack.node_api import JWT_SECRET, SCOPED_JWT_SECRET

CONVERSATION_CREATE_SCOPE = "conversation:create"
CONVERSATION_PERMISSIONS_SCOPE = "conversation:permissions"


def stable_oid(label: str) -> ObjectId:
    """The same label always maps to the same ObjectId, so seeds are reproducible across runs."""
    return ObjectId(hashlib.sha1(label.encode()).hexdigest()[:24])  # noqa: S324 - not a security use


@dataclass(frozen=True)
class Actor:
    name: str
    user_id: str
    org_id: str
    email: str
    role: str = "member"
    disabled: bool = False

    @property
    def oid(self) -> ObjectId:
        return ObjectId(self.user_id)

    @property
    def org_oid(self) -> ObjectId:
        return ObjectId(self.org_id)


@dataclass
class Directory:
    """Seeds orgs and users and signs their tokens."""

    db: Database
    actors: dict[str, Actor] = field(default_factory=dict)
    orgs: dict[str, str] = field(default_factory=dict)

    def org(self, name: str) -> str:
        if name not in self.orgs:
            org_id = stable_oid(f"org:{name}")
            now = datetime.now(timezone.utc)
            self.db["org"].insert_one(
                {
                    "_id": org_id,
                    "slug": f"org-{name}",
                    "registeredName": f"Org {name}",
                    "shortName": name,
                    "domain": f"{name}.example.com",
                    "contactEmail": f"admin@{name}.example.com",
                    "accountType": "business",
                    "onBoardingStatus": "configured",
                    "isDeleted": False,
                    "createdAt": now,
                    "updatedAt": now,
                    "__v": 0,
                }
            )
            self.orgs[name] = str(org_id)
        return self.orgs[name]

    def user(self, name: str, org: str = "acme", *, role: str = "member", disabled: bool = False, kind: str = "human") -> Actor:
        org_id = self.org(org)
        user_id = stable_oid(f"user:{org}:{name}")
        email = f"{name.lower()}@{org}.example.com"
        now = datetime.now(timezone.utc)
        self.db["users"].insert_one(
            {
                "_id": user_id,
                "slug": f"user-{org}-{name}",
                "orgId": ObjectId(org_id),
                "fullName": f"User {name}",
                "firstName": name,
                "email": email,
                "hasLoggedIn": True,
                "kind": kind,
                "isDisabled": disabled,
                "role": role,
                "isDeleted": False,
                "createdAt": now,
                "updatedAt": now,
                "__v": 0,
            }
        )
        actor = Actor(name=name, user_id=str(user_id), org_id=org_id, email=email, role=role, disabled=disabled)
        self.actors[f"{org}:{name}"] = actor
        return actor

    def actors_by_id(self, user_id: str) -> Actor:
        return next(a for a in self.actors.values() if a.user_id == user_id)

    def set_disabled(self, actor: Actor, disabled: bool) -> None:
        self.db["users"].update_one({"_id": actor.oid}, {"$set": {"isDisabled": disabled}})

    def delete(self, actor: Actor) -> None:
        self.db["users"].update_one({"_id": actor.oid}, {"$set": {"isDeleted": True}})

    # ---- tokens ----------------------------------------------------------------------------

    @staticmethod
    def session_token(actor: Actor, expires_in: int = 3600) -> str:
        """The session JWT the sign-in flow would issue (``authJwtGenerator``)."""
        now = int(time.time())
        return jwt.encode(
            {
                "userId": actor.user_id,
                "orgId": actor.org_id,
                "email": actor.email,
                "fullName": f"User {actor.name}",
                "accountType": "business",
                "role": actor.role,
                "iat": now,
                "exp": now + expires_in,
            },
            JWT_SECRET,
            algorithm="HS256",
        )

    @staticmethod
    def scoped_token(
        actor: Actor,
        scopes: tuple[str, ...] = (CONVERSATION_CREATE_SCOPE,),
        *,
        org_id: str | None = None,
        expires_in: int = 3600,
    ) -> str:
        """A token for the ``/internal/...`` routes (Slack bot, service integrations): identity by email."""
        now = int(time.time())
        payload = {"email": actor.email, "scopes": list(scopes), "iat": now, "exp": now + expires_in}
        payload["orgId"] = org_id if org_id is not None else actor.org_id
        return jwt.encode(payload, SCOPED_JWT_SECRET, algorithm="HS256")

    @staticmethod
    def service_token(actor: Actor, scopes: tuple[str, ...], *, expires_in: int = 3600, **claims: object) -> str:
        """What a Node-to-Python call carries: the actor's ``userId`` and ``orgId`` plus service scopes."""
        now = int(time.time())
        payload = {"userId": actor.user_id, "orgId": actor.org_id, "scopes": list(scopes), "iat": now, "exp": now + expires_in, **claims}
        return jwt.encode(payload, SCOPED_JWT_SECRET, algorithm="HS256")

    @staticmethod
    def conversation_permissions_token(actor: Actor) -> str:
        return Directory.scoped_token(actor, (CONVERSATION_PERMISSIONS_SCOPE,))
