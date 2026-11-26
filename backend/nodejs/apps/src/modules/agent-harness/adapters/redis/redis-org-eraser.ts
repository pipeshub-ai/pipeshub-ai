import type { Cluster, Redis } from 'ioredis';
import { IRedisConnectionProvider } from '../../../../libs/services/redis/connectionProvider.interface';
import { OrgRedisEraser } from '../../ports/principal-erasure';

const SCAN_COUNT = 1000;

/**
 * The Redis keys that name a deleted org (CLEANUP-G1): the worker's per-run
 * spend (`harness:spend:{<org>:<run>}`) and Node's sign-in revocations
 * (`harness:signin:revoked:<org>…`), under any key namespace. Live streams
 * are keyed by run and go with each session's erasure. On a cluster SCAN
 * sees one node's keys only, so every master is scanned; each key is then
 * deleted through the cluster client, which routes it to its slot.
 */
export class RedisOrgEraser implements OrgRedisEraser {
  constructor(private readonly provider: IRedisConnectionProvider) {}

  async eraseOrg(orgId: string): Promise<number> {
    if (!/^[A-Za-z0-9_.:-]+$/.test(orgId)) return 0;
    const client = this.provider.getClient();
    const nodes: Redis[] = this.provider.isCluster
      ? (client as Cluster).nodes('master')
      : [client as Redis];
    let erased = 0;
    for (const node of nodes) {
      for (const pattern of [
        `*harness:spend:{${orgId}:*`,
        `*harness:signin:revoked:${orgId}`,
        `*harness:signin:revoked:${orgId}:*`,
      ]) {
        let cursor = '0';
        do {
          const [next, keys] = await node.scan(
            cursor,
            'MATCH',
            pattern,
            'COUNT',
            SCAN_COUNT,
          );
          cursor = next;
          for (const key of keys) erased += await client.del(key);
        } while (cursor !== '0');
      }
    }
    return erased;
  }
}
