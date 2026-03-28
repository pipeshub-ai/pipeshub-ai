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
        assert LinearGraphQLOperations.QUERIES["viewer"]["fragments"] == []

    def test_issues_has_fragments(self):
        frags = LinearGraphQLOperations.QUERIES["issues"]["fragments"]
        assert "IssueFields" in frags
        assert "UserFields" in frags


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
        assert "organization" in result
        assert "fragment" not in result

    def test_query_with_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "teams")
        assert "fragment TeamFields" in result
        assert "fragment UserFields" in result
        assert "query teams" in result

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

    def test_issues_query_includes_comment_fragments(self):
        result = LinearGraphQLOperations.get_operation_with_fragments("query", "issues")
        assert "fragment CommentFields" in result

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
