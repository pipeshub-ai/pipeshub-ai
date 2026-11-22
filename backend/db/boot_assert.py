"""Boot-time schema check for a service. ``requirements`` is ``{branch: {"requires": n, "code": n}}``
(written into each build artifact by CI): refuse to become ready when migrate has not run
(``epoch < requires``) or the code is older than a contracted schema (``min_code_epoch > code``).
Branches in the database but not listed are ignored."""


def schema_problems(rows: dict[str, tuple[int, int]], requirements: dict[str, dict[str, int]]) -> list[str]:
    """``rows`` is ``{branch: (epoch, min_code_epoch)}`` from kernel.schema_epoch."""
    problems = []
    for branch, req in requirements.items():
        epoch, min_code = rows.get(branch, (0, 0))
        if epoch < req["requires"]:
            problems.append(f"schema {branch} epoch {epoch} < required {req['requires']}")
        if min_code > req["code"]:
            problems.append(f"schema {branch} min_code_epoch {min_code} > code {req['code']}")
    return problems
