import type { TFunction } from 'i18next';

export interface ServiceMeta {
  key: string;
  icon?: string;
  logoSlug?: string;
  label: string;
  description: string;
}

export interface Deployment {
  kvStoreType: string;
  messageBrokerType: string;
  graphDbType: string;
  vectorDbType?: string;
  // The cache mode the Node process runs: REDIS_MODE ('standalone', 'cluster',
  // 'memorydb', ...), or 'postgres' when the KV store and broker are PostgreSQL.
  redisMode?: string;
}

function redisLabel(mode: string | undefined, t: TFunction): string {
  if (mode === 'postgres') return t('workspace.services.infra.postgres.label');
  const baseLabel = t('workspace.services.infra.redis.label');
  if (mode === 'cluster') {
    return `${baseLabel} (${t('workspace.services.infra.redis.modeCluster')})`;
  }
  if (mode === 'memorydb') {
    return `${baseLabel} (${t('workspace.services.infra.redis.modeMemorydb')})`;
  }
  return baseLabel;
}

// `infra` is the services map of GET /api/v1/health: it reports `postgres` in
// place of `mongodb` when PostgreSQL is the document store.
export function buildInfraServices(
  deployment: Deployment | null,
  infra: Record<string, string> | null,
  t: TFunction,
): ServiceMeta[] {
  const services: ServiceMeta[] = [];

  const redisDesc = deployment?.kvStoreType === 'redis'
    ? t('workspace.services.infra.redis.descriptionWithKvStore')
    : t('workspace.services.infra.redis.description');
  services.push({
    key: 'redis',
    logoSlug: deployment?.redisMode === 'postgres' ? 'postgresql' : 'redis',
    label: redisLabel(deployment?.redisMode, t),
    description: redisDesc,
  });

  if (infra && 'postgres' in infra) {
    services.push({ key: 'postgres', logoSlug: 'postgresql', label: t('workspace.services.infra.postgres.label'), description: t('workspace.services.infra.postgres.description') });
  } else {
    services.push({ key: 'mongodb', logoSlug: 'mongodb', label: t('workspace.services.infra.mongodb.label'), description: t('workspace.services.infra.mongodb.description') });
  }

  const brokerType = deployment?.messageBrokerType;
  const brokerSlug = brokerType === 'redis' ? 'redis' : brokerType === 'postgres' ? 'postgresql' : 'apachekafka';
  services.push({ key: 'messageBroker', logoSlug: brokerSlug, label: t('workspace.services.infra.messageBroker.label'), description: t('workspace.services.infra.messageBroker.description') });

  if (deployment?.kvStoreType === 'etcd') {
    services.push({ key: 'KVStoreservice', logoSlug: 'etcd', label: t('workspace.services.infra.etcd.label'), description: t('workspace.services.infra.etcd.description') });
  }

  const graphType = deployment?.graphDbType;
  const graphSlug = graphType === 'neo4j' ? 'neo4j' : graphType === 'postgres' ? 'postgresql' : 'arangodb';
  services.push({ key: 'graphDb', logoSlug: graphSlug, label: t('workspace.services.infra.graphDb.label'), description: t('workspace.services.infra.graphDb.description') });

  // pgvector runs inside PostgreSQL.
  const vectorSlug = deployment?.vectorDbType === 'pgvector' ? 'postgresql' : deployment?.vectorDbType || 'qdrant';
  services.push({ key: 'vectorDb', logoSlug: vectorSlug, label: t('workspace.services.infra.vectorDb.label'), description: t('workspace.services.infra.vectorDb.description') });

  return services;
}
