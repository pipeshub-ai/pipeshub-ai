"""Telling a machine identity from a person, in a graph query.

`kind` on the user node is the field that decides this, and it is what these
fragments read. The address is the second half, and it is here for one reason:
`kind` is newer than the nodes. A node written before the field existed does
not carry it, and the backfill that stamps the service accounts among them runs
at startup (`app/migrations/service_account_kind_migration.py`). Until it has,
a service account's node looks like anyone else's to a filter on `kind` alone.

The address cannot be missing in the same way: it is written when the account is
created, and `assertReservedEmailDomainBelongsToServiceAccount`
(`user_management/constants/service-account.constants.ts`) refuses to store an
address in this domain on anything that is not a service account. So reading it
here can hide a service account early; it can never hide a colleague.

It decides nothing else. Everywhere the answer must be authoritative rather than
cautious — which account an agent is bound to, say — `kind` alone is read.
"""

SERVICE_ACCOUNT_EMAIL_DOMAIN = "service.pipeshub.internal"
_SUFFIX = f"@{SERVICE_ACCOUNT_EMAIL_DOMAIN}"


def aql_not_a_service_account(variable: str) -> str:
    """An AQL `FILTER` body keeping only the people in `variable`.

    A missing attribute compares as null in AQL, which is not `'service'`, so a
    node from before `kind` passes the first half — the address is what catches
    it.

    `RIGHT`, not `ENDS_WITH`: AQL has no `ENDS_WITH`, only `STARTS_WITH`, and
    not `LIKE` either here — the suffix would have to be escaped against `%`
    and `_`. `RIGHT` on a missing address gives the empty string, which is not
    the suffix, so a node with no address is kept rather than dropped.
    """
    return (
        f"{variable}.kind != 'service' "
        f"AND RIGHT(LOWER({variable}.email), {len(_SUFFIX)}) != '{_SUFFIX}'"
    )


def cypher_not_a_service_account(variable: str) -> str:
    """The same, as a Cypher predicate.

    Both null arms are load-bearing, and for a different reason than in AQL: a
    comparison against a missing property is null, a null predicate drops the
    row, and these would quietly empty the listing of everyone who was written
    before `kind` existed.
    """
    return (
        f"({variable}.kind IS NULL OR {variable}.kind <> 'service') "
        f"AND ({variable}.email IS NULL "
        f"OR NOT toLower({variable}.email) ENDS WITH '{_SUFFIX}')"
    )
