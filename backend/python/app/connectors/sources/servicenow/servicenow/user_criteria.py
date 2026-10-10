"""ServiceNow user criteria, evaluated against the users of one sync.

A user_criteria record lists users, groups, roles, companies, departments and
locations. Without Match All a user matching any listed value matches; with it
"every condition [is] required" (ServiceNow docs, User Criteria form). A grant per
listed principal reproduces only the first, so a criteria that needs more is
evaluated here and granted user by user.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict

from app.sources.external.servicenow.models import TableAPIRecord

_USER_ATTRIBUTE_FIELDS = ("company", "department", "location")
CRITERIA_FIELDS = ("user", "group", "role", *_USER_ATTRIBUTE_FIELDS, "match_all", "advanced", "active")


def _ids(value: object) -> frozenset[str]:
    if not value or not isinstance(value, str):
        return frozenset()
    return frozenset(v.strip() for v in value.split(",") if v.strip())


def _flag(value: object, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() == "true"


class CriteriaRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    sys_id: str
    conditions: dict[str, frozenset[str]]
    match_all: bool = False
    advanced: bool = False
    active: bool = True
    record: TableAPIRecord

    @classmethod
    def from_record(cls, record: TableAPIRecord) -> CriteriaRule:
        conditions = {
            field: ids
            for field in ("user", "group", "role", *_USER_ATTRIBUTE_FIELDS)
            if (ids := _ids(record.get(field)))
        }
        return cls(
            sys_id=record.get("sys_id"),
            conditions=conditions,
            match_all=_flag(record.get("match_all"), False),
            advanced=_flag(record.get("advanced"), False),
            active=_flag(record.get("active"), True),
            record=record,
        )

    @property
    def grants_follow_groups(self) -> bool:
        """Whether one grant per listed principal reaches exactly the users this matches."""
        values = sum(len(ids) for ids in self.conditions.values())
        return self.active and not self.advanced and values > 0 and (not self.match_all or values == 1)


class UserDirectory:
    """Active users of one sync with what criteria match on: attributes, groups, roles."""

    def __init__(
        self,
        users: Mapping[str, TableAPIRecord],
        group_members: Mapping[str, set[str]],
        role_members: Mapping[str, set[str]],
    ) -> None:
        self.user_ids = frozenset(users)
        self._members = {"group": group_members, "role": role_members}
        self._by_attribute: dict[str, dict[str, set[str]]] = {f: defaultdict(set) for f in _USER_ATTRIBUTE_FIELDS}
        for sys_id, row in users.items():
            for field in _USER_ATTRIBUTE_FIELDS:
                value = row.get(field)
                if value and isinstance(value, str):
                    self._by_attribute[field][value].add(sys_id)

    def _holders(self, field: str, value: str) -> set[str]:
        if field == "user":
            return {value} & self.user_ids
        if field in self._members:
            return set(self._members[field].get(value, set())) & self.user_ids
        return set(self._by_attribute[field].get(value, set()))

    def matching(self, rule: CriteriaRule) -> set[str] | None:
        """The active users the rule matches; None when it cannot be evaluated."""
        if not rule.active:
            return set()
        if rule.advanced:
            return None
        per_field = []
        for field, values in rule.conditions.items():
            holders = [self._holders(field, v) for v in values]
            per_field.append(set.intersection(*holders) if rule.match_all else set().union(*holders))
        if not per_field:
            # No condition and no script: ServiceNow's "Any User" criteria.
            return set(self.user_ids)
        return set.intersection(*per_field) if rule.match_all else set().union(*per_field)

    def granted(self, rules: Iterable[CriteriaRule | None]) -> set[str]:
        """Users any rule grants. A rule that is missing or cannot be evaluated grants nobody."""
        users: set[str] = set()
        for rule in rules:
            matched = self.matching(rule) if rule else None
            users |= matched or set()
        return users

    def denied(self, rules: Iterable[CriteriaRule | None]) -> set[str]:
        """Users any rule denies. A rule that is missing or cannot be evaluated denies everyone."""
        users: set[str] = set()
        for rule in rules:
            matched = self.matching(rule) if rule else None
            if matched is None:
                return set(self.user_ids)
            users |= matched
        return users


class KnowledgeBaseCriteria(BaseModel):
    """The user criteria ids of one knowledge base, by mtom table."""

    can_read: list[str] = []
    cannot_read: list[str] = []
    can_contribute: list[str] = []
    cannot_contribute: list[str] = []

    @property
    def all_ids(self) -> list[str]:
        return self.can_read + self.cannot_read + self.can_contribute + self.cannot_contribute

    def audience(
        self, directory: UserDirectory, rules: Mapping[str, CriteriaRule]
    ) -> tuple[set[str], set[str]]:
        """(readers, writers). Cannot Read wins over Can Read (ServiceNow docs,
        "Managing access to knowledge bases and knowledge articles"); it removes
        contributors too, because a write grant here also reads."""
        denied_read = directory.denied(rules.get(c) for c in self.cannot_read)
        writers = (
            directory.granted(rules.get(c) for c in self.can_contribute)
            - directory.denied(rules.get(c) for c in self.cannot_contribute)
            - denied_read
        )
        readers = directory.granted(rules.get(c) for c in self.can_read) - denied_read - writers
        return readers, writers
