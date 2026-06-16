"""
Scope helpers for the GitLab connector.

Responsibilities:
- Pick the correct ``list_groups`` / ``list_projects`` scope kwargs based on the
  authenticated user's role (admin, auditor, regular member).
- Implement the auditor-empty fallback: when a pure EE Auditor hits the documented
  GitLab known issue where ``all_available=True`` returns 0 rows, retry with
  ``min_access_level=10`` and then expand descendants to recover inherited subgroups.
- Emit the one-shot auditor fallback warning.
- Static pickers for the filter-options auditor fallback (groups/projects).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.sources.client.gitlab.gitlab import GitLabResponse

if TYPE_CHECKING:
    from app.connectors.sources.gitlab1.connector import GitLabConnector


class ScopeHelper:
    """Role-based scope selection and auditor-fallback logic for GitLab listing calls."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Scope kwargs pickers
    # ------------------------------------------------------------------

    def list_projects_scope_kwargs(self) -> dict[str, object]:
        """Pick the right ``list_projects`` scope flag for the current user.

        - Regular member → ``membership=True`` (projects with a membership row).
        - Admin or Auditor → ``{}`` (no flag; API default = "all visible").
          Their cross-instance access flows from a user-level flag, not a
          per-project access_level row, so ``membership=True`` / ``min_access_level``
          would return empty for pure admins/auditors.

        Note: ``_paged_list_projects_with_role_fallback`` retries with
        ``membership=True`` when the auditor primary scope returns empty,
        as the GitLab-documented workaround.
        """
        if self.c._is_admin or self.c._is_auditor:
            return {}
        return {"membership": True}

    def list_groups_scope_kwargs(self) -> dict[str, object]:
        """Pick the primary ``list_groups`` scope flag for the current user.

        - Regular member → ``min_access_level=10`` (groups where the caller is Guest+).
        - Admin / EE Auditor → ``all_available=True`` (every group the caller can read
          via the user-level admin/auditor flag, not just groups with a member row).

        IMPORTANT — Auditor caveat: GitLab documents a known issue where the auditor
        flag does not actually grant read access through most listing endpoints.
        ``_paged_list_groups_with_role_fallback`` retries with ``min_access_level=10``
        when the auditor primary scope returns empty.

        See: https://docs.gitlab.com/administration/auditor_users/
        """
        if self.c._is_admin or self.c._is_auditor:
            return {"all_available": True}
        return {"min_access_level": 10}

    # ------------------------------------------------------------------
    # One-shot auditor fallback warning
    # ------------------------------------------------------------------

    def warn_auditor_fallback_once(self, kind: str) -> None:
        """Log a single actionable WARN when the auditor primary scope yields nothing.

        Fires at most once per connector lifetime (guarded by
        ``c._auditor_fallback_warned``) to avoid spamming the same message on every
        paged sweep.  ``kind`` is "groups" or "projects" for operator correlation.
        """
        if self.c._auditor_fallback_warned:
            return
        self.c._auditor_fallback_warned = True
        self.logger.warning(
            "GitLab auditor token returned 0 %s with the documented auditor scope. "
            "This matches GitLab's known issue: 'Due to a known issue, [auditor] users "
            "must have the Reporter, Developer, Maintainer, or Owner role to perform "
            "read-only tasks' (https://docs.gitlab.com/administration/auditor_users/). "
            "Falling back to membership-scoped listing so any %s where the auditor has "
            "an explicit Reporter+ row still sync. "
            "To sync the full instance, grant the auditor Reporter+ at the top-level "
            "group(s), or configure the connector with an Admin token instead.",
            kind,
            kind,
        )

    # ------------------------------------------------------------------
    # Role-fallback list helpers
    # ------------------------------------------------------------------

    async def paged_list_groups_with_role_fallback(
        self,
        *args: Any,
        progress_label: str,
        progress_every: int = 500,
        **kwargs: Any,
    ) -> GitLabResponse:
        """``list_groups`` with auditor-empty fallback and descendant expansion.

        Calls ``list_groups`` with the primary role scope.  If the caller is an
        auditor, the call succeeded, but returned 0 rows, retries once with
        ``min_access_level=10`` (the GitLab-documented workaround).  Then expands
        descendants of each fallback group to recover inherited subgroups.

        Caller-supplied scope kwargs (``min_access_level``, ``all_available``,
        ``owned``) override the role default and disable the fallback.
        """
        c = self.c
        primary = self.list_groups_scope_kwargs()
        caller_overrode_scope = any(k in kwargs for k in ("min_access_level", "all_available", "owned"))
        merged: dict[str, Any] = {**primary, **kwargs}
        res = await c.runtime.paged_list(
            c.data_source.list_groups,
            *args,
            progress_label=progress_label,
            progress_every=progress_every,
            **merged,
        )
        if not c._is_auditor or caller_overrode_scope or not res.success or res.data:
            return res

        self.warn_auditor_fallback_once("groups")
        fallback = {k: v for k, v in kwargs.items() if k != "all_available"}
        fallback["min_access_level"] = 10
        fb_res = await c.runtime.paged_list(
            c.data_source.list_groups,
            *args,
            progress_label=f"{progress_label} [auditor membership fallback]",
            progress_every=progress_every,
            **fallback,
        )
        if not fb_res.success or not fb_res.data:
            return fb_res

        # ``min_access_level=10`` only returns groups with an explicit member row;
        # expand descendants to recover subgroups whose access is inherited.
        expanded = await self.expand_groups_with_descendants(
            list(fb_res.data),
            progress_label=f"{progress_label} [auditor descendants]",
            progress_every=progress_every,
        )
        return GitLabResponse(success=True, data=expanded)

    async def paged_list_projects_with_role_fallback(
        self,
        *args: Any,
        progress_label: str,
        progress_every: int = 500,
        **kwargs: Any,
    ) -> GitLabResponse:
        """``list_projects`` with auditor-empty fallback.

        Same contract as ``paged_list_groups_with_role_fallback`` but for the
        projects endpoint.  Primary auditor scope is no flag (rely on the documented
        auditor read-all).  Fallback is ``membership=True``.
        Caller-supplied ``membership`` / ``min_access_level`` / ``owned`` disables
        the fallback.
        """
        c = self.c
        primary = self.list_projects_scope_kwargs()
        caller_overrode_scope = any(k in kwargs for k in ("membership", "min_access_level", "owned"))
        merged: dict[str, Any] = {**primary, **kwargs}
        res = await c.runtime.paged_list(
            c.data_source.list_projects,
            *args,
            progress_label=progress_label,
            progress_every=progress_every,
            **merged,
        )
        if not c._is_auditor or caller_overrode_scope or not res.success or res.data:
            return res

        self.warn_auditor_fallback_once("projects")
        fallback = dict(kwargs)
        fallback["membership"] = True
        return await c.runtime.paged_list(
            c.data_source.list_projects,
            *args,
            progress_label=f"{progress_label} [auditor membership fallback]",
            progress_every=progress_every,
            **fallback,
        )

    async def expand_groups_with_descendants(
        self,
        base_groups: list[Any],
        *,
        progress_label: str,
        progress_every: int = 500,
        descendant_kwargs: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Merge each base group's full descendant subtree into a deduped list.

        Closes the GitLab gap where ``list_groups(min_access_level=10)`` omits
        subgroups whose access is inherited from an ancestor's Reporter+ membership.
        Only invoked from auditor fallback paths; extra round-trips are bounded by
        the auditor's explicit-membership set, which is small by definition.

        Dedupes by ``full_path`` (canonical GitLab identifier).  Preserves order —
        base groups first, then descendants in GitLab's response order.
        """
        if not base_groups:
            return base_groups

        def _key(g: Any) -> str | None:
            fp = getattr(g, "full_path", None)
            return str(fp) if fp else None

        seen: set[str] = set()
        out: list[Any] = []
        # Snapshot parents up front so newly-appended descendants are not re-walked.
        parents: list[tuple[str, Any]] = []
        for g in base_groups:
            key = _key(g)
            if key is None or key in seen:
                continue
            seen.add(key)
            out.append(g)
            parents.append((key, getattr(g, "id", None) or key))

        kw = dict(descendant_kwargs or {})
        kw.setdefault("min_access_level", 10)
        for parent_key, parent_id in parents:
            res = await self.c.runtime.paged_list(
                self.c.data_source.list_descendant_groups,
                parent_id,
                progress_label=f"{progress_label} (parent={parent_key})",
                progress_every=progress_every,
                **kw,
            )
            if not res.success:
                self.logger.warning(
                    "%s: descendant walk failed for parent=%s: %s",
                    progress_label,
                    parent_key,
                    res.error,
                )
                continue
            for d in res.data or []:
                key = _key(d)
                if key is None or key in seen:
                    continue
                seen.add(key)
                out.append(d)
        return out

    # ------------------------------------------------------------------
    # Filter-options picker fallbacks (static, no side-effects)
    # ------------------------------------------------------------------

    @staticmethod
    def picker_kwargs_for_auditor_groups_fallback(
        list_kwargs: dict[str, object],
    ) -> dict[str, object]:
        """Swap ``all_available`` for ``min_access_level=10`` in group picker kwargs.

        Used by the group picker to retry once when the auditor primary scope
        returns 0 rows.
        """
        out = {k: v for k, v in list_kwargs.items() if k != "all_available"}
        out["min_access_level"] = 10
        return out

    @staticmethod
    def picker_kwargs_for_auditor_projects_fallback(
        list_kwargs: dict[str, object],
    ) -> dict[str, object]:
        """Swap auditor primary (no scope) for ``min_access_level=10`` in project picker kwargs.

        ``min_access_level=10`` rather than ``membership=True`` to avoid the GitLab
        Guest-omission quirk on some self-managed builds.
        """
        out = dict(list_kwargs)
        out["min_access_level"] = 10
        return out
