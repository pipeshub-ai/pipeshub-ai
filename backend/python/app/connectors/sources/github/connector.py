"""
Personal GitHub connector.

Thin wrapper over ``GitHubTeamsConnector`` — the canonical implementation for
all GitHub sync logic (repos, issues, PRs, code, streaming) lives in
``app.connectors.sources.github_teams``. This mirrors the exact pattern
``gitlab_personal/connector.py`` uses over ``gitlab/connector.py``.

Sync scope is deliberately the SAME as the team connector's: every repository
the token can read. Only two things differ.

Overridden here:
- Repo discovery: ``list_user_repos(type="all")`` — owner, organization member
  and collaborator — instead of enumerating orgs and their repos. Account-
  anchored rather than org-anchored, which is why there is no org filter; the
  explicit ``REPO_IDS`` path is identical to the team connector's, so public
  repositories sync on both once named.
- Permissions: every record group is granted to a single internal
  ``ConnectorGroup`` (via ``ensure_connector_group_permission``) and to nothing
  else — no per-collaborator edges, and no visibility-derived ORG
  grant either. This is the real difference between the connectors: "I vouch
  for this content entering my workspace" versus "mirror GitHub's ACLs".
  Anyone needing the latter for an org repo should use the team connector.
- User sync is skipped entirely (no org member enumeration, no email
  resolution phases — there is only one user, the connector creator).
- Filters: repository filter only.

BREAKING CHANGE (pre-release, no migration shipped): re-basing on
``GitHubTeamsConnector`` moved external IDs from URL-derived to
``repo.id``-anchored — ``external_group_id`` from ``repo.url`` to
``str(repo.id)``, and ``external_record_id`` from ``issue.url`` to
``{repo.id}/issues/{number}``. Records written by the pre-refactor connector
are unreachable under the new scheme and would be re-created as duplicates.
This is accepted because the connector has no production installs; if that
changes, a migration rewriting both fields must land before upgrading.
"""

from __future__ import annotations

from logging import Logger

from app.sources.external.github.github_async import GhObject

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, PermissionModel
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO, IconPaths
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType, OAuthScopeConfig
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterOperator,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.github.common.apps import GithubApp
from app.connectors.sources.github_teams.connector import AUTHORIZE_URL, TOKEN_URL, GitHubTeamsConnector
from app.connectors.sources.github_teams.projects import ProjectsSync
from app.models.permission import Permission

AUTH_REDIRECT_URI = "connectors/oauth/callback/Github"


class GitHubPersonalProjectsSync(ProjectsSync):
    """``ProjectsSync`` variant for the personal connector.

    Overrides every hook that builds per-principal permissions so a repo — and
    its work-items/PRs/code children — is granted access through the single
    internal ``ConnectorGroup`` and nothing else.
    """

    async def _sync_repo_members(
        self, owner: str, repo: str, repo_obj: GhObject | None = None,
    ) -> list[Permission]:
        """Route all repo access through the ConnectorGroup — no collaborator/team fetch.

        ``repo_obj`` (used by the team connector to bind individual-repo
        collaborators) is deliberately ignored: personal access is the
        ConnectorGroup, never GitHub's ACL.
        """
        permission = self.c.creator_user_permission()
        return [permission] if permission is not None else []

    def _visibility_permissions(self, repo: GhObject) -> list[Permission]:
        """No visibility-derived grants on a personal connector.

        The team connector mirrors GitHub's ACL, so a public repo legitimately
        becomes ``Permission(READ, ORG)`` — that is its real audience. A
        personal connector makes the opposite promise: the creator vouches for
        content entering their workspace, and access is whoever is in
        ``ConnectorGroup``. An ORG grant breaks that promise in a way removing
        someone from the group cannot undo, which is precisely the single-edge
        revocation the group exists to provide.

        Every other personal-identity connector (gitlab_personal, dropbox,
        jira/confluence personal, outlook) emits zero ORG grants; inheriting
        this hook made GitHub the sole exception, and by accident rather than
        by decision.
        """
        return []

    async def _resolve_repos_with_filters(self) -> list[GhObject]:
        """Personal repo discovery: every repo the authenticated account reaches.

        ``type="all"`` is owner + organization_member + collaborator, matching
        what every other Personal connector means by scope — "content my account
        can see, into my personal workspace" — rather than "content I own". An
        earlier ownership gate here rejected org and public repositories even
        though the token could read them, which contradicted the repo picker
        (shared with the team connector) and made a selected repo sync nothing
        but an error line.

        Applies the ``REPO_IDS`` filter only: the team connector's org filter
        exists because it enumerates *by org*, while this one enumerates *by
        account*. On the explicit path both connectors behave identically —
        ``get_repo`` succeeding is the access check, so any readable repo,
        public ones included, is in scope.
        """
        c = self.c
        sf = c.sync_filters
        repo_f = sf.get(SyncFilterKey.REPO_IDS) if sf else None
        repo_vals = list(repo_f.value) if (repo_f and not repo_f.is_empty()) else []  # type: ignore[arg-type]
        repo_op = repo_f.operator_value if repo_vals else None
        repo_in = repo_vals if repo_op == FilterOperator.IN else []
        repo_not_in = repo_vals if repo_op == FilterOperator.NOT_IN else []

        if repo_in:
            by_id: dict[int, GhObject] = {}
            for full_name in repo_in:
                if "/" not in full_name:
                    self.logger.error("Skipping malformed repo filter value (expected owner/repo): %s", full_name)
                    continue
                owner, name = full_name.split("/", 1)
                res = await c.runtime.ds_call(c.data_source.get_repo, owner, name)
                if not res.success or not res.data:
                    self.logger.error("Repository not found or inaccessible: %s (%s)", full_name, res.error)
                    continue
                by_id[int(res.data.id)] = res.data
            return list(by_id.values())

        res = await c.runtime.ds_call(c.data_source.list_user_repos, None, "all")
        if not res.success:
            self.logger.error("list_user_repos failed: %s", res.error)
            return []
        candidates = list(res.data or [])
        if repo_not_in:
            excluded = set(repo_not_in)
            candidates = [r for r in candidates if getattr(r, "full_name", None) not in excluded]
        return candidates



@(
    ConnectorBuilder("Github")
    .in_group("Github")
    .with_description("Sync content from your Github instance")
    .with_categories(["Knowledge Management"])
    .with_scopes([ConnectorScope.PERSONAL.value])
    .with_permission_model(PermissionModel.APP_LEVEL)
    .with_auth(
        [
            AuthBuilder.type(AuthType.OAUTH).oauth(
                connector_name="Github",
                authorize_url=AUTHORIZE_URL,
                token_url=TOKEN_URL,
                redirect_uri=AUTH_REDIRECT_URI,
                scopes=OAuthScopeConfig(personal_sync=["user", "repo"], team_sync=[], agent=[]),
                fields=[
                    AuthField(
                        name="clientId",
                        display_name="Application (Client) ID",
                        placeholder="Enter your Github Application ID",
                        description="The Application (Client) ID from Github OAuth Registration",
                    ),
                    AuthField(
                        name="clientSecret",
                        display_name="Client Secret",
                        placeholder="Enter your Github Client Secret",
                        description="The Client Secret from Github OAuth Registration",
                        field_type="PASSWORD",
                        is_secret=True,
                    ),
                ],
                app_description="OAuth application for accessing Github services",
                app_categories=["Knowledge Management"],
            )
        ]
    )
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)
    .configure(
        lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.GITHUB.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink("Github API Docs", "https://docs.github.com/en", "docs"))
        .add_documentation_link(DocumentationLink("Pipeshub Documentation", "https://docs.pipeshub.com/connectors/github/github", "pipeshub"))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_sync_support(True)
        .add_filter_field(FilterField(
            name=SyncFilterKey.REPO_IDS.value,
            display_name="Repositories",
            description="Limit sync to specific repositories (full_name, e.g. my-username/my-repo)",
            filter_type=FilterType.MULTISELECT, category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.ISSUES.value,
            display_name="Index Issues",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.MERGE_REQUESTS.value,
            display_name="Index Pull Requests",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.CODE_FILES.value,
            display_name="Index Code Files",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_agent_support(False)
    )
    .build_decorator()
)
class GithubConnector(GitHubTeamsConnector):
    """Personal GitHub connector.

    Permission model:
      - No GitHub user directory is synced.
      - An internal ``AppUserGroup`` (name: ``ConnectorGroup``, scoped by
        ``connector_id``) is materialized once per sync. The connector's
        creator is the sole USER -> GROUP member edge.
      - Every repo / work-items / PRs / code-repository ``RecordGroup``
        receives a GROUP permission edge pointing at that internal group,
        instead of per-collaborator edges.
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> None:
        super().__init__(
            logger, data_entities_processor, data_store_provider,
            config_service, connector_id, scope, created_by,
        )
        self.app = GithubApp(connector_id)
        self.connector_name = Connectors.GITHUB.value
        # Replace the default ProjectsSync with the personal variant so repo
        # discovery and permission-building route through ConnectorGroup.
        self.projects = GitHubPersonalProjectsSync(self)

    def creator_user_permission(self) -> Permission | None:
        """Override: emit a GROUP permission on the shared ConnectorGroup.

        The team connector grants the creator direct USER access on repos as
        a fallback; the personal connector instead routes every record-group
        permission through the single internal ``AppUserGroup``
        (``ConnectorGroup``) materialized by ``ensure_connector_group_permission``,
        so that granting/revoking access to all synced content is a single-edge
        operation, not an N-record fan-out.
        """
        return self._connector_group_permission

    async def run_sync(self) -> None:
        """Sync every repo the configured account reaches; skip org member/email sync."""
        try:
            self.record_sync_point.org_id = self.data_entities_processor.org_id
            await self.repos.timestamps.cancel()
            await self.runtime.refresh_token_if_needed()
            self.logger.info("Starting GitHub Personal sync")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "github", self.connector_id, self.logger
            )
            # Force a fresh ConnectorGroup upsert each run so re-runs after the
            # creator email is rotated pick up the new identity instead of
            # reusing a stale cached permission.
            self._connector_group_permission = None

            if not self.creator_email and self.created_by:
                await self._load_creator_email()

            if not self.creator_email:
                self.logger.warning(
                    "GitHub Personal connector %s: no creator email — "
                    "record groups will sync without user permissions",
                    self.connector_id,
                )
            else:
                await self.ensure_connector_group_permission()

            self.logger.info("Starting sync of GitHub repositories")
            await self.projects.sync_all_repos()
            self.repos.timestamps.schedule()
        except Exception as e:
            self.logger.error("Error in GitHub Personal sync: %s", e, exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> BaseConnector:
        """Factory method to create a GitHub Personal connector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return GithubConnector(
            logger, data_entities_processor, data_store_provider,
            config_service, connector_id, scope, created_by,
        )
