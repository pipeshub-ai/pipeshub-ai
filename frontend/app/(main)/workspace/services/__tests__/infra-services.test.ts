import { describe, it, expect } from 'vitest';
import type { TFunction } from 'i18next';

import { buildInfraServices, type Deployment } from '../infra-services';

const t = ((key: string) => key) as unknown as TFunction;

const rows = (deployment: Deployment, infra: Record<string, string> | null) =>
  buildInfraServices(deployment, infra, t).map(({ key, logoSlug }) => [key, logoSlug]);

describe('buildInfraServices', () => {
  it('lists the default stack with its vendor logos', () => {
    expect(
      rows(
        { kvStoreType: 'redis', messageBrokerType: 'kafka', graphDbType: 'neo4j', vectorDbType: 'qdrant' },
        { redis: 'healthy', mongodb: 'healthy', messageBroker: 'healthy', graphDb: 'healthy', vectorDb: 'healthy' },
      ),
    ).toEqual([
      ['redis', 'redis'],
      ['mongodb', 'mongodb'],
      ['messageBroker', 'apachekafka'],
      ['graphDb', 'neo4j'],
      ['vectorDb', 'qdrant'],
    ]);
  });

  it('shows the PostgreSQL document store instead of MongoDB, with PostgreSQL logos', () => {
    // GET /api/v1/health on a PostgreSQL-only install.
    expect(
      rows(
        {
          kvStoreType: 'postgres',
          messageBrokerType: 'postgres',
          graphDbType: 'postgres',
          vectorDbType: 'pgvector',
          redisMode: 'postgres',
        },
        { redis: 'healthy', messageBroker: 'healthy', graphDb: 'healthy', vectorDb: 'healthy', postgres: 'healthy' },
      ),
    ).toEqual([
      ['redis', 'postgresql'],
      ['postgres', 'postgresql'],
      ['messageBroker', 'postgresql'],
      ['graphDb', 'postgresql'],
      ['vectorDb', 'postgresql'],
    ]);
  });

  it('draws the cache logo from the published cache mode, not the KV store type', () => {
    const pg = { kvStoreType: 'postgres', messageBrokerType: 'postgres', graphDbType: 'postgres', vectorDbType: 'pgvector' };
    expect(rows({ ...pg, redisMode: 'postgres' }, null)[0]).toEqual(['redis', 'postgresql']);
    expect(rows({ ...pg, redisMode: 'standalone' }, null)[0]).toEqual(['redis', 'redis']);
  });

  it('labels the PostgreSQL cache as PostgreSQL, which names its logo for screen readers', () => {
    const label = (redisMode: string) =>
      buildInfraServices({ kvStoreType: 'postgres', messageBrokerType: 'postgres', graphDbType: 'postgres', redisMode }, null, t)[0].label;
    expect(label('postgres')).toBe('workspace.services.infra.postgres.label');
    expect(label('standalone')).toBe('workspace.services.infra.redis.label');
  });

  it('keeps the MongoDB row when the health answer is not in yet', () => {
    expect(
      rows({ kvStoreType: 'redis', messageBrokerType: 'redis', graphDbType: 'arangodb' }, null).map(([key]) => key),
    ).toEqual(['redis', 'mongodb', 'messageBroker', 'graphDb', 'vectorDb']);
  });
});
