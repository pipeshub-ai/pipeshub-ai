/**
 * CLEANUP-G1 (b): an org's Redis keys are found on every master of a cluster,
 * not only the node the client happens to SCAN, and never another org's.
 */
import { expect } from 'chai';
import { RedisOrgEraser } from '../../../src/modules/agent-harness/adapters/redis/redis-org-eraser';

const globToRegExp = (glob: string): RegExp =>
  new RegExp(`^${glob.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*')}$`);

class FakeNode {
  constructor(readonly keys: Set<string>) {}
  async scan(_cursor: string, _match: string, pattern: string): Promise<[string, string[]]> {
    const re = globToRegExp(pattern);
    return ['0', [...this.keys].filter((key) => re.test(key))];
  }
}

const provider = (isCluster: boolean, nodes: FakeNode[]) => {
  const del = async (key: string): Promise<number> => {
    for (const node of nodes) if (node.keys.delete(key)) return 1;
    return 0;
  };
  const client = isCluster
    ? { nodes: (role: string) => (role === 'master' ? nodes : []), del }
    : { scan: nodes[0]!.scan.bind(nodes[0]), del };
  return { isCluster, getClient: () => client } as never;
};

describe('CLEANUP-G1 (b): RedisOrgEraser', () => {
  const keysOf = () => [
    new FakeNode(new Set(['ns:harness:spend:{org-1:run-a}', 'ns:harness:spend:{org-2:run-b}'])),
    new FakeNode(new Set(['ns:harness:signin:revoked:org-1:u:u1', 'ns:harness:signin:revoked:org-1', 'ns:harness:signin:revoked:org-10'])),
  ];

  it('on a cluster, scans every master and deletes only the org\'s keys', async () => {
    const nodes = keysOf();
    expect(await new RedisOrgEraser(provider(true, nodes)).eraseOrg('org-1')).to.equal(3);
    expect([...nodes[0]!.keys, ...nodes[1]!.keys]).to.deep.equal(['ns:harness:spend:{org-2:run-b}', 'ns:harness:signin:revoked:org-10']);
  });

  it('on a standalone Redis, scans the one node', async () => {
    const [node] = keysOf();
    expect(await new RedisOrgEraser(provider(false, [node!])).eraseOrg('org-1')).to.equal(1);
    expect([...node!.keys]).to.deep.equal(['ns:harness:spend:{org-2:run-b}']);
  });

  it('refuses an org id that would widen the pattern', async () => {
    expect(await new RedisOrgEraser(provider(false, keysOf())).eraseOrg('org-*')).to.equal(0);
  });
});
