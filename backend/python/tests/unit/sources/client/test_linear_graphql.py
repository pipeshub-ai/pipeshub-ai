import re

import pytest

from app.sources.client.linear.graphql_op import LinearGraphQLOperations


class TestFragments:
    def test_fragments_dict_is_not_empty(self):
        assert len(LinearGraphQLOperations.FRAGMENTS) > 0

    def test_user_fields_fragment_exists(self):
        assert "UserFields" in LinearGraphQLOperations.FRAGMENTS

    def test_team_fields_fragment_exists(self):
        assert "TeamFields" in LinearGraphQLOperations.FRAGMENTS

    def test_issue_fields_fragment_exists(self):
        assert "IssueFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_fields_fragment_exists(self):
        assert "ProjectFields" in LinearGraphQLOperations.FRAGMENTS

    def test_comment_fields_fragment_exists(self):
        assert "CommentFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_comment_fields_fragment_exists(self):
        assert "ProjectCommentFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_update_comment_fields_exists(self):
        assert "ProjectUpdateCommentFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_milestone_fields_exists(self):
        assert "ProjectMilestoneFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_update_fields_exists(self):
        assert "ProjectUpdateFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_external_link_fields_exists(self):
        assert "ProjectExternalLinkFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_document_fields_exists(self):
        assert "ProjectDocumentFields" in LinearGraphQLOperations.FRAGMENTS

    def test_each_fragment_contains_fragment_keyword(self):
        for name, frag in LinearGraphQLOperations.FRAGMENTS.items():
            assert "fragment" in frag, f"Fragment {name} missing 'fragment' keyword"

    def test_user_fields_has_email(self):
        assert "email" in LinearGraphQLOperations.FRAGMENTS["UserFields"]

    def test_issue_fields_references_user_fields(self):
        assert "...UserFields" in LinearGraphQLOperations.FRAGMENTS["IssueFields"]

    def test_issue_fields_references_team_fields(self):
        assert "...TeamFields" in LinearGraphQLOperations.FRAGMENTS["IssueFields"]

    def test_issue_fields_references_comment_fields(self):
        assert "...CommentFields" in LinearGraphQLOperations.FRAGMENTS["IssueFields"]


class TestQueries:
    def test_queries_dict_not_empty(self):
        assert len(LinearGraphQLOperations.QUERIES) > 0

    def test_viewer_query_exists(self):
        assert "viewer" in LinearGraphQLOperations.QUERIES

    def test_teams_query_exists(self):
        assert "teams" in LinearGraphQLOperations.QUERIES

    def test_users_query_exists(self):
        assert "users" in LinearGraphQLOperations.QUERIES

    def test_issues_query_exists(self):
        assert "issues" in LinearGraphQLOperations.QUERIES

    def test_issue_query_exists(self):
        assert "issue" in LinearGraphQLOperations.QUERIES

    def test_projects_query_exists(self):
        assert "projects" in LinearGraphQLOperations.QUERIES

    def test_issue_search_query_exists(self):
        assert "issueSearch" in LinearGraphQLOperations.QUERIES

    def test_trashed_issues_query_exists(self):
        assert "trashedIssues" in LinearGraphQLOperations.QUERIES

    def test_trashed_projects_query_exists(self):
        assert "trashedProjects" in LinearGraphQLOperations.QUERIES

    def test_organization_query_exists(self):
        assert "organization" in LinearGraphQLOperations.QUERIES

    def test_comment_query_exists(self):
        assert "comment" in LinearGraphQLOperations.QUERIES

    def test_attachment_query_exists(self):
        assert "attachment" in LinearGraphQLOperations.QUERIES

    def test_document_query_exists(self):
        assert "document" in LinearGraphQLOperations.QUERIES

    def test_attachments_query_exists(self):
        assert "attachments" in LinearGraphQLOperations.QUERIES

    def test_documents_query_exists(self):
        assert "documents" in LinearGraphQLOperations.QUERIES

    def test_project_query_exists(self):
        assert "project" in LinearGraphQLOperations.QUERIES

    def test_each_query_has_required_keys(self):
        for name, op in LinearGraphQLOperations.QUERIES.items():
            assert "query" in op, f"Query {name} missing 'query'"
            assert "fragments" in op, f"Query {name} missing 'fragments'"
            assert "description" in op, f"Query {name} missing 'description'"

    def test_viewer_has_no_fragments(self):
        assert LinearGraphQLOperations.QUERIES["viewer"]["fragments"] == ["UserFields"]

    def test_issues_has_fragments(self):
        # The list query uses its own lighter fragment and nothing else.
        frags = LinearGraphQLOperations.QUERIES["issues"]["fragments"]
        assert frags == ["IssueListFields"]


class TestMutations:
    def test_mutations_dict_not_empty(self):
        assert len(LinearGraphQLOperations.MUTATIONS) > 0

    def test_issue_create_exists(self):
        assert "issueCreate" in LinearGraphQLOperations.MUTATIONS

    def test_issue_update_exists(self):
        assert "issueUpdate" in LinearGraphQLOperations.MUTATIONS

    def test_issue_delete_exists(self):
        assert "issueDelete" in LinearGraphQLOperations.MUTATIONS

    def test_comment_create_exists(self):
        assert "commentCreate" in LinearGraphQLOperations.MUTATIONS

    def test_project_create_exists(self):
        assert "projectCreate" in LinearGraphQLOperations.MUTATIONS

    def test_project_update_exists(self):
        assert "projectUpdate" in LinearGraphQLOperations.MUTATIONS

    def test_each_mutation_has_required_keys(self):
        for name, op in LinearGraphQLOperations.MUTATIONS.items():
            assert "query" in op, f"Mutation {name} missing 'query'"
            assert "fragments" in op, f"Mutation {name} missing 'fragments'"
            assert "description" in op, f"Mutation {name} missing 'description'"

    def test_issue_delete_no_fragments(self):
        assert LinearGraphQLOperations.MUTATIONS["issueDelete"]["fragments"] == []

    def test_issue_create_has_mutation_keyword(self):
        assert "mutation" in LinearGraphQLOperations.MUTATIONS["issueCreate"]["query"]


class TestGetOperationWithFragments:
    def test_query_with_no_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "viewer")
        assert "viewer" in result
        assert "fragment UserFields" in result

    def test_query_with_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "teams")
        assert "fragment TeamFields" in result
        assert "query teams" in result
        # Members are listed inline, 100 at a time with a cursor; the rest come from
        # the teamMembers operation, so the UserFields fragment is no longer attached.
        assert "fragment UserFields" not in result
        assert "members(first: 100)" in result
        assert "guest" in result

    def test_mutation_with_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueCreate")
        assert "fragment IssueFields" in result
        assert "fragment CommentFields" in result
        assert "mutation IssueCreate" in result

    def test_mutation_with_no_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueDelete")
        assert "mutation IssueDelete" in result

    def test_invalid_query_raises(self):
        with pytest.raises(ValueError, match="not found"):
            LinearGraphQLOperations.get_operation_with_fragments("query", "nonexistent")

    def test_invalid_mutation_raises(self):
        with pytest.raises(ValueError, match="not found"):
            LinearGraphQLOperations.get_operation_with_fragments("mutation", "nonexistent")

    def test_project_query_includes_all_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "project")
        assert "fragment ProjectFields" in result
        assert "fragment ProjectExternalLinkFields" in result
        assert "fragment ProjectDocumentFields" in result
        assert "fragment ProjectMilestoneFields" in result
        assert "fragment ProjectCommentFields" in result
        assert "fragment ProjectUpdateFields" in result
        assert "fragment ProjectUpdateCommentFields" in result

    def test_issues_query_uses_list_fragment(self) -> None:
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "issues")
        assert "fragment IssueListFields on Issue" in result
        assert "...IssueListFields" in result
        assert "fragment CommentFields" not in result
        assert "fragment IssueFields" not in result
        assert "$orderBy: PaginationOrderBy" in result
        assert "orderBy: $orderBy" in result

    def test_result_is_string(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "viewer")
        assert isinstance(result, str)

    def test_fragments_precede_query(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "teams")
        frag_idx = result.index("fragment TeamFields")
        query_idx = result.index("query teams")
        assert frag_idx < query_idx


class TestGetAllOperations:
    def test_returns_dict(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert isinstance(result, dict)

    def test_has_queries_key(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert "queries" in result

    def test_has_mutations_key(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert "mutations" in result

    def test_has_fragments_key(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert "fragments" in result

    def test_queries_match_class_attribute(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert result["queries"] is LinearGraphQLOperations.QUERIES

    def test_mutations_match_class_attribute(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert result["mutations"] is LinearGraphQLOperations.MUTATIONS

    def test_fragments_match_class_attribute(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert result["fragments"] is LinearGraphQLOperations.FRAGMENTS


class TestGetOperationWithFragmentsMissingFragment:
    """Cover the branch where a fragment name is listed but not defined in FRAGMENTS."""

    def test_operation_with_nonexistent_fragment_skipped(self):
        """Cover branch 849->848: fragment_name NOT in cls.FRAGMENTS."""
        # Temporarily inject an operation that references a non-existent fragment
        original_queries = LinearGraphQLOperations.QUERIES.copy()
        try:
            LinearGraphQLOperations.QUERIES["_test_op"] = {
                "query": "query testOp { field }",
                "fragments": ["NonExistentFragment", "UserFields"],
                "description": "Test operation with missing fragment",
            }
            result = LinearGraphQLOperations.get_operation_with_fragments("query", "_test_op")
            # NonExistentFragment should be skipped; only UserFields included
            assert "fragment UserFields" in result
            assert "NonExistentFragment" not in result
            assert "query testOp" in result
        finally:
            LinearGraphQLOperations.QUERIES = original_queries

    def test_operation_with_all_nonexistent_fragments(self):
        """Cover branch where ALL fragment names are missing from FRAGMENTS."""
        original_queries = LinearGraphQLOperations.QUERIES.copy()
        try:
            LinearGraphQLOperations.QUERIES["_test_op2"] = {
                "query": "query testOp2 { field }",
                "fragments": ["DoesNotExist1", "DoesNotExist2"],
                "description": "Test operation with all missing fragments",
            }
            result = LinearGraphQLOperations.get_operation_with_fragments("query", "_test_op2")
            # No fragments found, so result is just the query
            assert "fragment" not in result
            assert "query testOp2" in result
        finally:
            LinearGraphQLOperations.QUERIES = original_queries


# =============================================================================
# Merged from test_linear_graphql_full_coverage.py
# =============================================================================

class TestFragmentsFullCoverage:
    def test_fragments_dict_is_not_empty(self):
        assert len(LinearGraphQLOperations.FRAGMENTS) > 0

    def test_user_fields_fragment_exists(self):
        assert "UserFields" in LinearGraphQLOperations.FRAGMENTS

    def test_team_fields_fragment_exists(self):
        assert "TeamFields" in LinearGraphQLOperations.FRAGMENTS

    def test_issue_fields_fragment_exists(self):
        assert "IssueFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_fields_fragment_exists(self):
        assert "ProjectFields" in LinearGraphQLOperations.FRAGMENTS

    def test_comment_fields_fragment_exists(self):
        assert "CommentFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_comment_fields_fragment_exists(self):
        assert "ProjectCommentFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_update_comment_fields_exists(self):
        assert "ProjectUpdateCommentFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_milestone_fields_exists(self):
        assert "ProjectMilestoneFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_update_fields_exists(self):
        assert "ProjectUpdateFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_external_link_fields_exists(self):
        assert "ProjectExternalLinkFields" in LinearGraphQLOperations.FRAGMENTS

    def test_project_document_fields_exists(self):
        assert "ProjectDocumentFields" in LinearGraphQLOperations.FRAGMENTS

    def test_each_fragment_contains_fragment_keyword(self):
        for name, frag in LinearGraphQLOperations.FRAGMENTS.items():
            assert "fragment" in frag, f"Fragment {name} missing 'fragment' keyword"

    def test_user_fields_has_email(self):
        assert "email" in LinearGraphQLOperations.FRAGMENTS["UserFields"]

    def test_issue_fields_references_user_fields(self):
        assert "...UserFields" in LinearGraphQLOperations.FRAGMENTS["IssueFields"]

    def test_issue_fields_references_team_fields(self):
        assert "...TeamFields" in LinearGraphQLOperations.FRAGMENTS["IssueFields"]

    def test_issue_fields_references_comment_fields(self):
        assert "...CommentFields" in LinearGraphQLOperations.FRAGMENTS["IssueFields"]


class TestQueriesFullCoverage:
    def test_queries_dict_not_empty(self):
        assert len(LinearGraphQLOperations.QUERIES) > 0

    def test_viewer_query_exists(self):
        assert "viewer" in LinearGraphQLOperations.QUERIES

    def test_teams_query_exists(self):
        assert "teams" in LinearGraphQLOperations.QUERIES

    def test_users_query_exists(self):
        assert "users" in LinearGraphQLOperations.QUERIES

    def test_issues_query_exists(self):
        assert "issues" in LinearGraphQLOperations.QUERIES

    def test_issue_query_exists(self):
        assert "issue" in LinearGraphQLOperations.QUERIES

    def test_projects_query_exists(self):
        assert "projects" in LinearGraphQLOperations.QUERIES

    def test_issue_search_query_exists(self):
        assert "issueSearch" in LinearGraphQLOperations.QUERIES

    def test_trashed_issues_query_exists(self):
        assert "trashedIssues" in LinearGraphQLOperations.QUERIES

    def test_trashed_projects_query_exists(self):
        assert "trashedProjects" in LinearGraphQLOperations.QUERIES

    def test_organization_query_exists(self):
        assert "organization" in LinearGraphQLOperations.QUERIES

    def test_comment_query_exists(self):
        assert "comment" in LinearGraphQLOperations.QUERIES

    def test_attachment_query_exists(self):
        assert "attachment" in LinearGraphQLOperations.QUERIES

    def test_document_query_exists(self):
        assert "document" in LinearGraphQLOperations.QUERIES

    def test_attachments_query_exists(self):
        assert "attachments" in LinearGraphQLOperations.QUERIES

    def test_documents_query_exists(self):
        assert "documents" in LinearGraphQLOperations.QUERIES

    def test_project_query_exists(self):
        assert "project" in LinearGraphQLOperations.QUERIES

    def test_each_query_has_required_keys(self):
        for name, op in LinearGraphQLOperations.QUERIES.items():
            assert "query" in op, f"Query {name} missing 'query'"
            assert "fragments" in op, f"Query {name} missing 'fragments'"
            assert "description" in op, f"Query {name} missing 'description'"

    def test_viewer_has_no_fragments(self):
        assert LinearGraphQLOperations.QUERIES["viewer"]["fragments"] == ["UserFields"]

    def test_issues_has_fragments(self):
        # The list query uses its own lighter fragment and nothing else.
        frags = LinearGraphQLOperations.QUERIES["issues"]["fragments"]
        assert frags == ["IssueListFields"]


class TestMutationsFullCoverage:
    def test_mutations_dict_not_empty(self):
        assert len(LinearGraphQLOperations.MUTATIONS) > 0

    def test_issue_create_exists(self):
        assert "issueCreate" in LinearGraphQLOperations.MUTATIONS

    def test_issue_update_exists(self):
        assert "issueUpdate" in LinearGraphQLOperations.MUTATIONS

    def test_issue_delete_exists(self):
        assert "issueDelete" in LinearGraphQLOperations.MUTATIONS

    def test_comment_create_exists(self):
        assert "commentCreate" in LinearGraphQLOperations.MUTATIONS

    def test_project_create_exists(self):
        assert "projectCreate" in LinearGraphQLOperations.MUTATIONS

    def test_project_update_exists(self):
        assert "projectUpdate" in LinearGraphQLOperations.MUTATIONS

    def test_each_mutation_has_required_keys(self):
        for name, op in LinearGraphQLOperations.MUTATIONS.items():
            assert "query" in op, f"Mutation {name} missing 'query'"
            assert "fragments" in op, f"Mutation {name} missing 'fragments'"
            assert "description" in op, f"Mutation {name} missing 'description'"

    def test_issue_delete_no_fragments(self):
        assert LinearGraphQLOperations.MUTATIONS["issueDelete"]["fragments"] == []

    def test_issue_create_has_mutation_keyword(self):
        assert "mutation" in LinearGraphQLOperations.MUTATIONS["issueCreate"]["query"]


class TestGetOperationWithFragmentsFullCoverage:
    def test_query_with_no_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "viewer")
        assert "viewer" in result
        assert "fragment UserFields" in result

    def test_query_with_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "teams")
        assert "fragment TeamFields" in result
        assert "query teams" in result
        # Members are listed inline, 100 at a time with a cursor; the rest come from
        # the teamMembers operation, so the UserFields fragment is no longer attached.
        assert "fragment UserFields" not in result
        assert "members(first: 100)" in result
        assert "guest" in result

    def test_mutation_with_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueCreate")
        assert "fragment IssueFields" in result
        assert "mutation IssueCreate" in result

    def test_mutation_with_no_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueDelete")
        assert "mutation IssueDelete" in result

    def test_invalid_query_raises(self):
        with pytest.raises(ValueError, match="not found"):
            LinearGraphQLOperations.get_operation_with_fragments("query", "nonexistent")

    def test_invalid_mutation_raises(self):
        with pytest.raises(ValueError, match="not found"):
            LinearGraphQLOperations.get_operation_with_fragments("mutation", "nonexistent")

    def test_project_query_includes_all_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "project")
        assert "fragment ProjectFields" in result
        assert "fragment ProjectExternalLinkFields" in result
        assert "fragment ProjectDocumentFields" in result
        assert "fragment ProjectMilestoneFields" in result
        assert "fragment ProjectCommentFields" in result
        assert "fragment ProjectUpdateFields" in result
        assert "fragment ProjectUpdateCommentFields" in result

    def test_issues_query_uses_list_fragment(self) -> None:
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "issues")
        assert "fragment IssueListFields on Issue" in result
        assert "...IssueListFields" in result
        assert "fragment CommentFields" not in result
        assert "fragment IssueFields" not in result
        assert "$orderBy: PaginationOrderBy" in result
        assert "orderBy: $orderBy" in result

    def test_result_is_string(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "viewer")
        assert isinstance(result, str)

    def test_fragments_precede_query(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "teams")
        frag_idx = result.index("fragment TeamFields")
        query_idx = result.index("query teams")
        assert frag_idx < query_idx


class TestGetAllOperationsFullCoverage:
    def test_returns_dict(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert isinstance(result, dict)

    def test_has_queries_key(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert "queries" in result

    def test_has_mutations_key(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert "mutations" in result

    def test_has_fragments_key(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert "fragments" in result

    def test_queries_match_class_attribute(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert result["queries"] is LinearGraphQLOperations.QUERIES

    def test_mutations_match_class_attribute(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert result["mutations"] is LinearGraphQLOperations.MUTATIONS

    def test_fragments_match_class_attribute(self):
        result = LinearGraphQLOperations.get_all_operations()
        assert result["fragments"] is LinearGraphQLOperations.FRAGMENTS


# =============================================================================
# Operations used by the sync: paging, id listings and fragment hygiene
# =============================================================================


def _full(kind, name) -> str:
    return LinearGraphQLOperations.get_operation_with_fragments(kind, name)


class TestOperationsAttachExactlyTheFragmentsTheyUse:
    """Linear rejects a document that defines a fragment it never spreads, and one
    that spreads a fragment it does not define."""

    @pytest.mark.parametrize("kind", ["query", "mutation"])
    def test_spread_fragments_equal_defined_fragments(self, kind) -> None:
        # Looked up when the test runs: other tests in this file rebind QUERIES.
        registry = LinearGraphQLOperations.QUERIES if kind == "query" else LinearGraphQLOperations.MUTATIONS
        assert registry
        for name in registry:
            document = _full(kind, name)
            spread = set(re.findall(r"\.\.\.(\w+)", document))
            defined = set(re.findall(r"fragment (\w+) on", document))
            assert spread == defined, f"{kind} {name}: spread {spread} but defined {defined}"

    @pytest.mark.parametrize("name", ["projectCreate", "projectUpdate"])
    def test_project_mutations_list_only_the_project_fragment(self, name) -> None:
        assert LinearGraphQLOperations.MUTATIONS[name]["fragments"] == ["ProjectFields"]
        document = _full("mutation", name)
        assert "fragment ProjectFields" in document
        assert "fragment UserFields" not in document
        assert "fragment TeamFields" not in document


class TestIssueListFragment:
    def test_leaves_out_what_has_its_own_listing(self) -> None:
        fragment = LinearGraphQLOperations.FRAGMENTS["IssueListFields"]
        assert "attachments" not in fragment
        assert "children" not in fragment
        assert "documents" not in fragment

    def test_nested_lists_are_bounded_and_resumable(self) -> None:
        fragment = " ".join(LinearGraphQLOperations.FRAGMENTS["IssueListFields"].split())
        assert "labels(first: 20)" in fragment
        assert "relations(first: 20)" in fragment
        assert "comments(first: 20)" in fragment
        # Every nested connection is asked for with an explicit page size.
        for connection in re.findall(r"(\w+)(\([^)]*\))? \{ nodes", fragment):
            assert "first:" in connection[1], f"{connection[0]} has no page size"
        # Comments and relations can be continued from where the first page stopped.
        for field in ("relations", "comments"):
            tail = fragment.split(f"{field}(first: 20)", 1)[1]
            closing = tail.index("pageInfo { hasNextPage endCursor }")
            assert "(first:" not in tail[:closing], f"{field} has no pageInfo of its own"

    def test_issue_search_uses_the_list_fragment_too(self) -> None:
        assert LinearGraphQLOperations.QUERIES["issueSearch"]["fragments"] == ["IssueListFields"]


class TestFollowUpPageOperations:
    """Operations that read the pages of a nested list the first response left out."""

    @pytest.mark.parametrize("name,parent,field", [
        ("issueComments", "issue", "comments"),
        ("issueAttachments", "issue", "attachments"),
        ("issueRelations", "issue", "relations"),
        ("issueDocuments", "issue", "documents"),
        ("teamMembers", "team", "members"),
        ("projectExternalLinks", "project", "externalLinks"),
        ("projectDocuments", "project", "documents"),
        ("projectMilestones", "project", "projectMilestones"),
        ("projectComments", "project", "comments"),
        ("projectUpdates", "project", "projectUpdates"),
        ("projectIssues", "project", "issues"),
        ("projectUpdateComments", "projectUpdate", "comments"),
    ])
    def test_pages_one_nested_list_of_one_parent(self, name, parent, field) -> None:
        document = " ".join(_full("query", name).split())
        assert f"query {name}($id: String!, $first: Int, $after: String)" in document
        assert f"{parent}(id: $id)" in document
        assert f"{field}(first: $first, after: $after)" in document
        assert "pageInfo { hasNextPage endCursor }" in document

    @pytest.mark.parametrize("name,root,filter_type", [
        ("issueIds", "issues", "IssueFilter"),
        ("projectIds", "projects", "ProjectFilter"),
        ("attachmentIds", "attachments", "AttachmentFilter"),
        ("documentIds", "documents", "DocumentFilter"),
        ("commentActivity", "comments", "CommentFilter"),
        ("projectUpdateActivity", "projectUpdates", "ProjectUpdateFilter"),
    ])
    def test_listings_page_a_root_connection(self, name, root, filter_type) -> None:
        document = " ".join(_full("query", name).split())
        assert f"$filter: {filter_type}" in document
        assert f"{root}(first: $first, after: $after, filter: $filter" in document
        assert "pageInfo { hasNextPage endCursor }" in document

    @pytest.mark.parametrize("name", ["issueIds", "projectIds"])
    def test_id_listings_can_include_archived_and_report_trashed(self, name) -> None:
        document = _full("query", name)
        assert "includeArchived: $includeArchived" in document
        assert "trashed" in document

    def test_issue_ids_carry_the_team_for_confirmation(self) -> None:
        document = " ".join(_full("query", "issueIds").split())
        assert "team { id }" in document

    def test_single_team_query_reports_archival(self) -> None:
        document = _full("query", "team")
        assert "team(id: $id)" in document
        assert "archivedAt" in document


def _documented_methods() -> list[tuple[str, str]]:
    return [(kind, name) for kind, table in (("query", LinearGraphQLOperations.QUERIES),
                                              ("mutation", LinearGraphQLOperations.MUTATIONS))
            for name in table]


@pytest.mark.parametrize("kind,name", _documented_methods())
async def test_the_data_source_names_each_operation_as_its_document_does(kind, name) -> None:
    """A GraphQL server runs the operation named in the request; one the document does not
    define is refused ("operation does not exist" on Linear)."""
    import inspect
    from unittest.mock import AsyncMock, MagicMock

    from app.sources.client.graphql.response import GraphQLResponse
    from app.sources.external.linear.linear import LinearDataSource

    client = MagicMock()
    client.get_client.return_value.execute = AsyncMock(return_value=GraphQLResponse(success=True, data={}))
    datasource = LinearDataSource(client)
    method = getattr(datasource, name, None)
    if method is None:
        pytest.skip(f"the data source has no {name} method")
    arguments = {
        p.name: ({} if "Dict" in str(p.annotation) else "x")
        for p in inspect.signature(method).parameters.values() if p.default is inspect.Parameter.empty
    }
    await method(**arguments)

    sent = client.get_client.return_value.execute.await_args.kwargs
    defined = re.findall(rf"\b{kind}\s+(\w+)", sent["query"])
    named = sent.get("operation_name")
    assert named in defined or (named is None and len(defined) == 1), (
        f"{name} sends {named!r}; the document defines {defined}")
