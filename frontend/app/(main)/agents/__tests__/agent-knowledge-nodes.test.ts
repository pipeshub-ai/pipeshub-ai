import { describe, expect, it } from 'vitest';
import {
  agentKnowledgeNodes,
  extractAgentKnowledgeCollectionRows,
  extractAgentKnowledgeConnectors,
  extractAgentKnowledgeDefaults,
} from '../api';

const agent = {
  knowledge: [
    { connectorId: 'jira-1', type: 'Jira', name: 'Jira' },
    {
      connectorId: 'drive-1',
      type: 'Drive',
      name: 'Drive',
      filtersParsed: {
        recordGroups: ['shared-drive'],
        records: ['folder-x'],
        nodes: [
          { id: 'shared-drive', name: 'Engineering drive', nodeType: 'recordGroup' },
          { id: 'folder-x', name: 'Specs', nodeType: 'folder' },
        ],
      },
    },
    { connectorId: 'kb-1', type: 'KB', name: 'Handbook' },
    { connectorId: 'kb-2', type: 'KB', name: 'Policies', filters: '{"records": ["policy-file"]}' },
  ],
} as never;

describe('agent knowledge limited to some nodes', () => {
  it('searches a limited source as its nodes and a whole one as itself', () => {
    expect(extractAgentKnowledgeDefaults(agent)).toEqual({
      apps: ['jira-1'],
      kb: ['kb-1'],
      recordGroups: ['shared-drive'],
      records: ['folder-x', 'policy-file'],
    });
  });

  it('lists the nodes of a limited connector in place of the connector', () => {
    expect(extractAgentKnowledgeConnectors(agent)).toEqual([
      { id: 'jira-1', label: 'Jira', connectorKind: 'Jira' },
      { id: 'shared-drive', label: 'Engineering drive', connectorKind: 'Drive', nodeType: 'recordGroup' },
      { id: 'folder-x', label: 'Specs', connectorKind: 'Drive', nodeType: 'folder' },
    ]);
  });

  it('lists the nodes of a limited collection, named by id when no name was saved', () => {
    expect(extractAgentKnowledgeCollectionRows(agent)).toEqual([
      { id: 'kb-1', name: 'Handbook', sourceType: 'KB' },
      { id: 'policy-file', name: 'policy-file', sourceType: 'KB', nodeType: 'folder' },
    ]);
  });

  it('does not read a list that names the source itself as a limit', () => {
    const legacy = { connectorId: 'kb-1', type: 'KB', filtersParsed: { recordGroups: ['kb-1'] } };
    expect(agentKnowledgeNodes(legacy)).toEqual({ recordGroups: [], records: [] });
  });

  it('reads unreadable stored filters as a whole source', () => {
    expect(agentKnowledgeNodes({ connectorId: 'x', filters: 'not json' })).toEqual({ recordGroups: [], records: [] });
  });
});
