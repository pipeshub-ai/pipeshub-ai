import { AgentRunDefinition } from '../../../../src/modules/agent-harness/domain/agent-definition';
import { AgentAccessPolicy } from '../../../../src/modules/agent-harness/ports/agent-access';

/** An agent with one toolset, one MCP server, an app and a KB, and web search. */
export const agentDefinition = (
  overrides: Partial<AgentRunDefinition> = {},
): AgentRunDefinition => ({
  agentKey: 'agent-1',
  definitionVersion: 7,
  models: [
    {
      modelKey: 'model-1',
      modelName: 'gpt-x',
      provider: 'openAI',
      isReasoning: true,
    },
    {
      modelKey: 'model-2',
      modelName: 'claude-x',
      provider: 'anthropic',
      isReasoning: true,
    },
  ],
  defaultReasoningEffort: 'medium',
  toolsets: [
    {
      name: 'jira',
      instanceId: 'inst-42',
      tools: [
        { name: 'search_issues', fullName: 'jira.search_issues' },
        { name: 'create_issue', fullName: 'jira.create_issue' },
      ],
    },
  ],
  mcpServers: [
    {
      instanceId: 'mcp-9',
      name: 'github',
      typeId: 'github',
      tools: [{ name: 'list_prs', fullName: 'mcp_github_list_prs' }],
    },
  ],
  knowledge: [
    { connectorId: 'app-drive', isKb: false },
    { connectorId: 'kb-1', isKb: true },
  ],
  skills: [{ name: 'xlsx', version: '3' }],
  webSearch: 'serper',
  ...overrides,
});

/** Grants `definition` for its own agent key to anyone. */
export const grantingAccess = (
  definition: (agentKey: string) => AgentRunDefinition = (agentKey) =>
    agentDefinition({ agentKey }),
): AgentAccessPolicy => ({
  authorizeRun: async (request) => definition(request.agentKey),
});

export const denyingAccess: AgentAccessPolicy = {
  authorizeRun: async () => null,
};
