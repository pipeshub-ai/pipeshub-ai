import { describe, it, expect } from 'vitest';
import { buildConnectorAppSidebarTree } from '../tree-builder';
import type { KnowledgeHubNode } from '../../types';

const APP_ID = 'app-1';

function node(
  id: string,
  parentId: string | null,
  extra: Partial<KnowledgeHubNode> = {}
): KnowledgeHubNode {
  return {
    id,
    name: id,
    nodeType: 'record',
    parentId,
    origin: 'CONNECTOR',
    hasChildren: false,
    ...extra,
  } as unknown as KnowledgeHubNode;
}

describe('buildConnectorAppSidebarTree', () => {
  it('nests children under the App by its bare id', () => {
    const tree = buildConnectorAppSidebarTree(APP_ID, [
      node('rg-1', APP_ID),
      node('rg-2', APP_ID),
    ]);

    expect(tree.map((n) => n.id)).toEqual(['rg-1', 'rg-2']);
  });

  it('nests a grandchild under its own parent, not under the App', () => {
    const tree = buildConnectorAppSidebarTree(APP_ID, [
      node('rg-1', APP_ID, { hasChildren: true }),
      node('rec-1', 'rg-1'),
    ]);

    expect(tree).toHaveLength(1);
    expect(tree[0].children?.map((c) => c.id)).toEqual(['rec-1']);
  });

  it('does not adopt rows carrying the retired prefixed parent id', () => {
    // The API emits bare ids. An `apps/<id>` fallback would re-root these and
    // make a wrong tree look right.
    const tree = buildConnectorAppSidebarTree(APP_ID, [node('rg-1', `apps/${APP_ID}`)]);

    expect(tree).toEqual([]);
  });

  it('does not fall back to re-rooting parentless rows', () => {
    const tree = buildConnectorAppSidebarTree(APP_ID, [node('stray', null)]);

    expect(tree).toEqual([]);
  });

  it('leaves the App row itself out of its own subtree', () => {
    const tree = buildConnectorAppSidebarTree(APP_ID, [
      node(APP_ID, null, { nodeType: 'app' }),
      node('rg-1', APP_ID),
    ]);

    expect(tree.map((n) => n.id)).toEqual(['rg-1']);
  });
});
