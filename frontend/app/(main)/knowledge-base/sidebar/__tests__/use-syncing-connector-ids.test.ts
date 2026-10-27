import { describe, it, expect } from 'vitest';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import { syncingIds } from '../use-syncing-connector-ids';

const connector = (partial: Partial<Connector>): Connector => ({ isActive: true, ...partial }) as Connector;

describe('syncingIds', () => {
  it('marks only active connectors that are syncing now', () => {
    const ids = syncingIds([
      connector({ _key: 'running', status: 'SYNCING' }),
      connector({ _key: 'full', status: 'full_syncing' }),
      connector({ _key: 'queued', status: 'QUEUED' }),
      connector({ _key: 'idle', status: 'IDLE' }),
      connector({ _key: 'off', status: 'SYNCING', isActive: false }),
      connector({ status: 'SYNCING' }),
    ]);
    expect([...ids].sort()).toEqual(['full', 'running']);
  });
});
