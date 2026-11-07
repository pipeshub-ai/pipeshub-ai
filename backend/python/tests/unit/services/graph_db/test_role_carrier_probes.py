"""``role_carrier_probes``: the person-role edges of a batch, by record, in
either edge shape and direction."""

from app.services.graph_db.common.utils import role_carrier_probes


def test_person_edges_in_either_shape_and_direction_name_their_record() -> None:
    edges = [
        {"_from": "records/r1", "_to": "users/u1", "edgeType": "ASSIGNED_TO"},
        {"_from": "users/u2", "_to": "records/r2", "edgeType": "MENTIONED_IN"},
        {"from_collection": "records", "from_id": "r3", "to_collection": "person", "to_id": "p",
         "edgeType": "AUTHORED_BY"},
        {"_from": "records/r1", "_to": "users/u9", "edgeType": "ASSIGNED_TO"},
    ]
    assert role_carrier_probes(edges) == [
        {"rid": "r1", "edge": "ASSIGNED_TO"}, {"rid": "r2", "edge": "MENTIONED_IN"}, {"rid": "r3", "edge": "AUTHORED_BY"},
    ]


def test_other_edges_are_not_roles() -> None:
    edges = [{"_from": "records/r1", "_to": "organizations/o", "edgeType": "FOR_ACCOUNT"},
             {"_from": "records/r1", "_to": "users/u", "edgeType": "SOMETHING_ELSE"}, {"edgeType": "ASSIGNED_TO"},
             {"_from": "records/r1", "_to": "records/r2", "edgeType": "ASSIGNED_TO"}]
    assert role_carrier_probes(edges) == []
