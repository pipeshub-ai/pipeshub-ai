"""Single definition of the role a team member holds on a KB through a team->KB edge.

The edge role is a grant to the team as a whole, including members who join later.
Legacy role-less edges fall back to the member's own team role capped at WRITER, so a
team OWNER never becomes a KB OWNER through a team. Every KB-derived query on both
backends builds its expression here so the rule has one implementation.
"""

TEAM_KB_ROLE_CAP = "WRITER"
BACKFILL_STAMP_ROLE = "READER"


def cypher_team_kb_role(edge_var: str, member_var: str) -> str:
    """Cypher expression: role of the user (``member_var`` USER->team edge) on the KB behind ``edge_var``."""
    return (
        f"CASE WHEN {edge_var}.role IS NOT NULL AND {edge_var}.role <> '' THEN {edge_var}.role "
        f"WHEN {member_var}.role = 'OWNER' THEN '{TEAM_KB_ROLE_CAP}' "
        f"ELSE {member_var}.role END"
    )


def aql_team_kb_role(edge_var: str, member_var: str) -> str:
    """AQL expression equivalent of :func:`cypher_team_kb_role`."""
    return (
        f'(({edge_var}.role != null AND {edge_var}.role != "") ? {edge_var}.role '
        f': ({member_var}.role == "OWNER" ? "{TEAM_KB_ROLE_CAP}" : {member_var}.role))'
    )
