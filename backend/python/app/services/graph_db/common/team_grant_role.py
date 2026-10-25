"""The role a user holds on a target (a knowledge base, record group or record) through a team.

The team -> target PERMISSION edge is the grant. Its role, when it has one, is what every
member gets: a member's role inside the team is about managing the team, not the target.
The share dialog writes team grants without a role ("teams do not have share roles"), so
such a grant gives each member their team role. Either way a team never makes anyone an
owner: a team OWNER (the oldest user of every org owns its "All" team) would otherwise own
every knowledge base shared with the team and could share it on or delete it, and the
owner guards count direct grants only. Any other role reads as no role: the hub's role
maps rank ADMIN and EDITOR above WRITER, and nothing validates a member role written
straight to the team edge.

Both backends build the expression here so the rule has one definition.
"""

from app.models.permission import RETIRED_ROLES

TEAM_GRANT_ROLE_CAP = "WRITER"
TEAM_GRANT_ROLES = ("OWNER", "WRITER", "READER")

_RETIRED_CYPHER = "[" + ", ".join(f"'{r}'" for r in sorted(RETIRED_ROLES)) + "]"
_RETIRED_AQL = "[" + ", ".join(f'"{r}"' for r in sorted(RETIRED_ROLES)) + "]"
_GRANT_ROLES_CYPHER = "[" + ", ".join(f"'{r}'" for r in TEAM_GRANT_ROLES) + "]"
_GRANT_ROLES_AQL = "[" + ", ".join(f'"{r}"' for r in TEAM_GRANT_ROLES) + "]"


def cypher_team_grant_role(grant_var: str, member_var: str) -> str:
    """Cypher expression for the role a team member gets from a team's grant.

    ``grant_var`` is the team -> target PERMISSION edge, ``member_var`` the user -> team one.
    """
    base = (
        f"(CASE WHEN {grant_var}.role IS NOT NULL AND {grant_var}.role <> '' "
        f"THEN {grant_var}.role ELSE {member_var}.role END)"
    )
    return (
        f"(CASE WHEN {base} = 'OWNER' THEN '{TEAM_GRANT_ROLE_CAP}' "
        f"WHEN {base} IN {_RETIRED_CYPHER} THEN 'READER' "
        f"WHEN {base} IN {_GRANT_ROLES_CYPHER} THEN {base} ELSE null END)"
    )


def aql_team_grant_role(grant_var: str, member_var: str) -> str:
    """AQL equivalent of :func:`cypher_team_grant_role`."""
    base = (
        f'(({grant_var}.role != null AND {grant_var}.role != "") '
        f"? {grant_var}.role : {member_var}.role)"
    )
    return (
        f'({base} == "OWNER" ? "{TEAM_GRANT_ROLE_CAP}" '
        f': ({base} IN {_RETIRED_AQL} ? "READER" '
        f": ({base} IN {_GRANT_ROLES_AQL} ? {base} : null)))"
    )
