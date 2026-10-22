"""Resolving which nodes may start a grants pass.

A seed must be granted to the user, non-strict, not deleted, inside a partition
the connector gate admits, and not beneath a hidden group. Strictness is the
load-bearing exclusion: a strict node needs every ancestor, so one below a gap
can never qualify and seeding it would over-share.

The gate is computed first and separately, because a grant inside an app the
user cannot reach must not produce a seed at all.
"""

# Apps the user can reach: a user-app relation, or any permission that reaches
# them. Both legs are OPTIONAL — a user whose only route is the relation would
# otherwise be dropped before seeds are considered — and each source is
# collected separately, since combining a bound variable with collect() in one
# WITH is an implicit grouping key that Cypher rejects.
#
# Both legs are scoped by orgId, as the provider's gate is. Without
# it this oracle admits another org's App whenever a shared principal holds a
# grant on it — orgs share group and org nodes — and since the oracle is what
# the gate is judged against, an org-blind oracle would bless an org-blind
# product.
GATE_CYPHER = """
MATCH (u:User {id: $user})
OPTIONAL MATCH (u)-[:USER_APP_RELATION]->(viaRelation:App)
WHERE viaRelation.orgId = $org
WITH u, collect(DISTINCT viaRelation.id) AS relApps
OPTIONAL MATCH (g)-[:PERMISSION]->(viaGrant:App)
WHERE g.id IN $grantees AND viaGrant.orgId = $org
WITH relApps, collect(DISTINCT viaGrant.id) AS grantApps
WITH [x IN relApps + grantApps WHERE x IS NOT NULL] AS gatedApps
"""

SEEDS_CYPHER = GATE_CYPHER + """
UNWIND gatedApps AS appId
MATCH (grantee)-[:PERMISSION]->(n)
WHERE grantee.id IN $grantees
  AND (n:Record OR n:RecordGroup)
  AND NOT coalesce(n.isDeleted, false)
  AND coalesce(n.accessRule, 'OPEN') = 'OPEN'
  AND EXISTS { (:App {id: appId})((a)-[:NODE_RELATION]->(b))+(n) }
RETURN DISTINCT appId, n.id AS seed
ORDER BY appId, seed
"""


async def gated_app_ids(session, user: str, org: str, grantees: list[str]) -> set[str]:
    result = await session.run(
        GATE_CYPHER + "RETURN gatedApps AS apps", user=user, org=org, grantees=grantees
    )
    row = await result.single()
    return set(row["apps"])


async def seeds_by_app(session, user: str, org: str, grantees: list[str]) -> dict[str, list[str]]:
    result = await session.run(SEEDS_CYPHER, user=user, org=org, grantees=grantees)
    grouped: dict[str, list[str]] = {}
    async for row in result:
        grouped.setdefault(row["appId"], []).append(row["seed"])
    return grouped
