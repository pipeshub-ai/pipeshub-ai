"""Tests for Neo4j / ArangoDB MCP server constants and mappings."""


class TestNeo4jMcpMappings:
    def test_agent_mcp_server_label_exists(self):
        from app.config.constants.neo4j import Neo4jLabel

        assert Neo4jLabel.AGENT_MCP_SERVERS.value == "AgentMcpServer"

    def test_agent_mcp_tool_label_exists(self):
        from app.config.constants.neo4j import Neo4jLabel

        assert Neo4jLabel.AGENT_MCP_TOOLS.value == "AgentMcpTool"

    def test_agent_has_mcp_server_relationship(self):
        from app.config.constants.neo4j import Neo4jRelationshipType

        assert Neo4jRelationshipType.AGENT_HAS_MCP_SERVER.value == "AGENT_HAS_MCP_SERVER"

    def test_mcp_server_has_tool_relationship(self):
        from app.config.constants.neo4j import Neo4jRelationshipType

        assert Neo4jRelationshipType.MCP_SERVER_HAS_TOOL.value == "MCP_SERVER_HAS_TOOL"

    def test_collection_to_label_mapping_mcp_servers(self):
        from app.config.constants.arangodb import CollectionNames
        from app.config.constants.neo4j import COLLECTION_TO_LABEL, Neo4jLabel

        assert (
            COLLECTION_TO_LABEL[CollectionNames.AGENT_MCP_SERVERS.value]
            == Neo4jLabel.AGENT_MCP_SERVERS.value
        )

    def test_collection_to_label_mapping_mcp_tools(self):
        from app.config.constants.arangodb import CollectionNames
        from app.config.constants.neo4j import COLLECTION_TO_LABEL, Neo4jLabel

        assert (
            COLLECTION_TO_LABEL[CollectionNames.AGENT_MCP_TOOLS.value]
            == Neo4jLabel.AGENT_MCP_TOOLS.value
        )

    def test_edge_to_relationship_agent_has_mcp_server(self):
        from app.config.constants.arangodb import CollectionNames
        from app.config.constants.neo4j import EDGE_COLLECTION_TO_RELATIONSHIP, Neo4jRelationshipType

        assert (
            EDGE_COLLECTION_TO_RELATIONSHIP[CollectionNames.AGENT_HAS_MCP_SERVER.value]
            == Neo4jRelationshipType.AGENT_HAS_MCP_SERVER.value
        )

    def test_edge_to_relationship_mcp_server_has_tool(self):
        from app.config.constants.arangodb import CollectionNames
        from app.config.constants.neo4j import EDGE_COLLECTION_TO_RELATIONSHIP, Neo4jRelationshipType

        assert (
            EDGE_COLLECTION_TO_RELATIONSHIP[CollectionNames.MCP_SERVER_HAS_TOOL.value]
            == Neo4jRelationshipType.MCP_SERVER_HAS_TOOL.value
        )

    def test_arango_collection_names_mcp(self):
        from app.config.constants.arangodb import CollectionNames

        assert CollectionNames.AGENT_MCP_SERVERS.value == "agentMcpServers"
        assert CollectionNames.AGENT_MCP_TOOLS.value == "agentMcpTools"
        assert CollectionNames.AGENT_HAS_MCP_SERVER.value == "agentHasMcpServer"
        assert CollectionNames.MCP_SERVER_HAS_TOOL.value == "mcpServerHasTool"
