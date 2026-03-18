"""Registry of NewRelic NerdGraph GraphQL operations and fragments.

NewRelic NerdGraph API: https://api.newrelic.com/graphiql
Documentation: https://docs.newrelic.com/docs/apis/nerdgraph/get-started/introduction-new-relic-nerdgraph/
"""

from typing import Any


class NewRelicGraphQLOperations:
    """Registry of NewRelic NerdGraph GraphQL operations and fragments."""

    # Common fragments
    FRAGMENTS: dict[str, str] = {
        "EntityFields": """
            fragment EntityFields on EntityOutline {
                guid
                name
                type
                entityType
                domain
                accountId
                reporting
                tags {
                    key
                    values
                }
            }
        """,
        "AccountFields": """
            fragment AccountFields on AccountOutline {
                id
                name
                reportingEventTypes
            }
        """,
    }

    # Query operations
    QUERIES: dict[str, dict[str, Any]] = {
        "list_accounts": {
            "query": """
                query listAccounts {
                    actor {
                        accounts {
                            ...AccountFields
                        }
                    }
                }
            """,
            "fragments": ["AccountFields"],
            "description": "List all accessible accounts",
        },
        "get_account": {
            "query": """
                query getAccount($accountId: Int!) {
                    actor {
                        account(id: $accountId) {
                            id
                            name
                            reportingEventTypes
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "Get a specific account by ID",
        },
        "nrql_query": {
            "query": """
                query nrqlQuery($accountId: Int!, $nrqlQuery: Nrql!) {
                    actor {
                        account(id: $accountId) {
                            nrql(query: $nrqlQuery) {
                                results
                            }
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "Execute a NRQL query against an account",
        },
        "list_entities": {
            "query": """
                query listEntities($queryString: String, $entityTypes: [EntitySearchQueryBuilderType!]) {
                    actor {
                        entitySearch(query: $queryString, queryBuilder: {type: $entityTypes}) {
                            count
                            results {
                                entities {
                                    ...EntityFields
                                }
                                nextCursor
                            }
                        }
                    }
                }
            """,
            "fragments": ["EntityFields"],
            "description": "Search for entities with optional filters",
        },
        "get_entity": {
            "query": """
                query getEntity($guid: EntityGuid!) {
                    actor {
                        entity(guid: $guid) {
                            guid
                            name
                            type
                            entityType
                            domain
                            accountId
                            reporting
                            tags {
                                key
                                values
                            }
                            ... on AlertableEntity {
                                alertSeverity
                                recentAlertViolations(count: 5) {
                                    alertSeverity
                                    label
                                    openedAt
                                    closedAt
                                    violationUrl
                                }
                            }
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "Get a specific entity by GUID",
        },
        "list_dashboards": {
            "query": """
                query listDashboards {
                    actor {
                        entitySearch(queryBuilder: {type: DASHBOARD}) {
                            count
                            results {
                                entities {
                                    guid
                                    name
                                    accountId
                                    tags {
                                        key
                                        values
                                    }
                                    ... on DashboardEntityOutline {
                                        dashboardParentGuid
                                        owner {
                                            email
                                            userId
                                        }
                                    }
                                }
                                nextCursor
                            }
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "List all dashboards",
        },
        "list_alert_policies": {
            "query": """
                query listAlertPolicies($accountId: Int!, $cursor: String) {
                    actor {
                        account(id: $accountId) {
                            alerts {
                                policiesSearch(cursor: $cursor) {
                                    nextCursor
                                    totalCount
                                    policies {
                                        id
                                        name
                                        incidentPreference
                                        accountId
                                    }
                                }
                            }
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "List alert policies for an account",
        },
        "list_synthetics_monitors": {
            "query": """
                query listSyntheticsMonitors {
                    actor {
                        entitySearch(queryBuilder: {type: SYNTHETIC_MONITOR}) {
                            count
                            results {
                                entities {
                                    guid
                                    name
                                    accountId
                                    tags {
                                        key
                                        values
                                    }
                                    ... on SyntheticMonitorEntityOutline {
                                        monitorType
                                        monitoredUrl
                                        period
                                        monitorSummary {
                                            locationsFailing
                                            locationsRunning
                                            status
                                            successRate
                                        }
                                    }
                                }
                                nextCursor
                            }
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "List synthetics monitors",
        },
        "get_application": {
            "query": """
                query getApplication($guid: EntityGuid!) {
                    actor {
                        entity(guid: $guid) {
                            ... on ApmApplicationEntity {
                                guid
                                name
                                accountId
                                language
                                runningAgentVersions {
                                    maxVersion
                                    minVersion
                                }
                                settings {
                                    apdexTarget
                                    serverSideConfig
                                }
                                apmSummary {
                                    apdexScore
                                    errorRate
                                    hostCount
                                    instanceCount
                                    responseTimeAverage
                                    throughput
                                    webResponseTimeAverage
                                    webThroughput
                                }
                                tags {
                                    key
                                    values
                                }
                            }
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "Get APM application details by GUID",
        },
    }

    # Mutation operations
    MUTATIONS: dict[str, dict[str, Any]] = {}

    @classmethod
    def get_operation_with_fragments(
        cls, operation_type: str, operation_name: str
    ) -> str:
        """Get a complete GraphQL operation with all required fragments."""
        operations = cls.QUERIES if operation_type == "query" else cls.MUTATIONS

        if operation_name not in operations:
            raise ValueError(
                f"Operation {operation_name} not found in {operation_type}s"
            )
        operation = operations[operation_name]
        fragments_needed = operation.get("fragments", [])

        # Collect all fragments (deduplicate while preserving order)
        seen: set[str] = set()
        fragment_definitions: list[str] = []
        for fragment_name in fragments_needed:
            if fragment_name in cls.FRAGMENTS and fragment_name not in seen:
                fragment_definitions.append(cls.FRAGMENTS[fragment_name])
                seen.add(fragment_name)

        # Combine fragments and operation
        if fragment_definitions:
            return (
                "\n\n".join(fragment_definitions)
                + "\n\n"
                + operation["query"]
            )
        return str(operation["query"])

    @classmethod
    def get_all_operations(cls) -> dict[str, dict[str, Any]]:
        """Get all available operations."""
        return {
            "queries": cls.QUERIES,
            "mutations": cls.MUTATIONS,
            "fragments": cls.FRAGMENTS,
        }
