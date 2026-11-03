import { describe, expect, it } from 'vitest';
import type { FlowNodeData } from '../../types';
import { extractAgentConfigFromFlow } from '../../extract-agent-config';
import {
  knowledgeSourceId,
  limitFromSelection,
  limitOf,
  sameLimit,
  selectionOf,
  withLimit,
} from '../knowledge-source-limit';

const node = (type: string, config: Record<string, unknown>): FlowNodeData => ({ id: 'n1', type, label: 'Source', config });
const named = (id: string) => ({ id, name: `name of ${id}`, nodeType: 'folder' });

describe('a knowledge node as one source', () => {
  it('is saved under the collection id or the connector instance id', () => {
    expect(knowledgeSourceId(node('kb-abc', { kbId: 'abc' }))).toBe('abc');
    expect(knowledgeSourceId(node('app-jira', { connectorInstanceId: 'jira-1' }))).toBe('jira-1');
  });

  it('is not one source when it groups several, or is not knowledge at all', () => {
    expect(knowledgeSourceId(node('kb-group', {}))).toBe('');
    expect(knowledgeSourceId(node('app-group', {}))).toBe('');
    expect(knowledgeSourceId(node('toolset-jira', { connectorInstanceId: 'x' }))).toBe('');
  });
});

describe('the limit of a source', () => {
  it('is read from what the node selected, else from its stored filters', () => {
    const stored = node('app-jira', { filters: { recordGroups: ['g1'], records: [], nodes: [named('g1')] } });
    expect(limitOf(stored)).toEqual({ recordGroups: ['g1'], records: [], nodes: [named('g1')] });
    const edited = node('app-jira', { selectedRecordGroups: [], selectedRecords: ['f1'], filters: { recordGroups: ['g1'] } });
    expect(limitOf(edited)).toMatchObject({ recordGroups: [], records: ['f1'] });
  });

  it('opens the picker on the whole source when there is none', () => {
    expect(selectionOf('jira-1', { recordGroups: [], records: [], nodes: [] })).toEqual({
      apps: ['jira-1'], kb: [], recordGroups: [], records: [],
    });
  });

  it('opens the picker on the nodes it lists', () => {
    expect(selectionOf('jira-1', { recordGroups: ['g1'], records: ['f1'], nodes: [] })).toEqual({
      apps: [], kb: [], recordGroups: ['g1'], records: ['f1'],
    });
  });

  it('is none when the whole source is selected', () => {
    const whole = { apps: ['jira-1'], kb: [], recordGroups: [], records: [] };
    expect(limitFromSelection('jira-1', whole, named)).toEqual({ recordGroups: [], records: [], nodes: [] });
  });

  it('lists the selected nodes with the names known for them', () => {
    const picked = { apps: [], kb: [], recordGroups: ['g1'], records: ['f1', 'unknown'] };
    const limit = limitFromSelection('jira-1', picked, (id) => (id === 'unknown' ? undefined : named(id)));
    expect(limit).toEqual({ recordGroups: ['g1'], records: ['f1', 'unknown'], nodes: [named('g1'), named('f1')] });
  });
});

describe('saving the dialog without a change', () => {
  it('sees the same nodes in another order as the same limit', () => {
    const saved = { recordGroups: ['g1'], records: ['f1', 'f2'], nodes: [named('f1')] };
    expect(sameLimit({ recordGroups: ['g1'], records: ['f2', 'f1'], nodes: [] }, saved)).toBe(true);
    expect(sameLimit({ recordGroups: [], records: ['f1', 'f2'], nodes: [] }, saved)).toBe(false);
  });

  it('sees the whole source as the same as no limit', () => {
    const whole = limitFromSelection('jira-1', { apps: ['jira-1'], kb: [] }, named);
    expect(sameLimit(whole, { recordGroups: [], records: [], nodes: [] })).toBe(true);
  });
});

describe('saving an agent', () => {
  const flowWith = (type: string, config: Record<string, unknown>) => ({
    nodes: [
      { id: 'core', data: { id: 'core', type: 'agent-core', label: 'Agent', config: {} } },
      { id: 'k1', data: { id: 'k1', type, label: 'Source', config } },
    ],
    edges: [{ id: 'e1', source: 'k1', target: 'core', targetHandle: 'knowledge' }],
  });
  const savedKnowledge = (type: string, config: Record<string, unknown>) => {
    const flow = flowWith(type, config);
    return extractAgentConfigFromFlow('Agent', flow.nodes as never, flow.edges as never, null).knowledge;
  };

  it('writes a connector limit, with the names of its nodes, into the source filters', () => {
    const limit = { recordGroups: ['g1'], records: ['f1'], nodes: [named('g1'), named('f1')] };
    const config = withLimit({ connectorInstanceId: 'jira-1', filters: { recordGroups: [], records: [] } }, limit);

    expect(savedKnowledge('app-jira', config)).toEqual([
      { connectorId: 'jira-1', filters: { recordGroups: ['g1'], records: ['f1'], nodes: [named('g1'), named('f1')] } },
    ]);
  });

  it('writes a collection limit the same way', () => {
    const limit = { recordGroups: [], records: ['f1'], nodes: [named('f1')] };
    const config = withLimit({ kbId: 'kb-1', filters: { records: [] } }, limit);

    expect(savedKnowledge('kb-kb-1', config)).toEqual([
      { connectorId: 'kb-1', filters: { recordGroups: [], records: ['f1'], nodes: [named('f1')] } },
    ]);
  });

  it('writes no limit for a source used as a whole', () => {
    const config = withLimit({ connectorInstanceId: 'jira-1' }, { recordGroups: [], records: [], nodes: [] });
    expect(savedKnowledge('app-jira', config)).toEqual([
      { connectorId: 'jira-1', filters: { recordGroups: [], records: [], nodes: [] } },
    ]);
  });
});
