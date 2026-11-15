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
  // Process-env setting (REDIS_MODE), not tied to kvStoreType -- 'standalone'
  // (default), 'cluster', or an EE-registered mode like 'memorydb'.
  redisMode?: string;
}

function redisLabel(mode: string | undefined, t: TFunction): string {
  const baseLabel = t('workspace.services.infra.redis.label');
  if (mode === 'cluster') {
    return `${baseLabel} (${t('workspace.services.infra.redis.modeCluster')})`;
  }
  if (mode === 'memorydb') {
    return `${baseLabel} (${t('workspace.services.infra.redis.modeMemorydb')})`;
  }
  return baseLabel;
}

export function buildInfraServices(deployment: Deployment | null, t: TFunction): ServiceMeta[] {
  const services: ServiceMeta[] = [];

  const redisDesc = deployment?.kvStoreType === 'redis'
    ? t('workspace.services.infra.redis.descriptionWithKvStore')
    : t('workspace.services.infra.redis.description');
  services.push({
    key: 'redis',
    logoSlug: 'redis',
    label: redisLabel(deployment?.redisMode, t),
    description: redisDesc,
  });

  services.push({ key: 'mongodb', logoSlug: 'mongodb', label: t('workspace.services.infra.mongodb.label'), description: t('workspace.services.infra.mongodb.description') });

  const brokerSlug = deployment?.messageBrokerType === 'redis' ? 'redis' : 'apachekafka';
  services.push({ key: 'messageBroker', logoSlug: brokerSlug, label: t('workspace.services.infra.messageBroker.label'), description: t('workspace.services.infra.messageBroker.description') });

  if (deployment?.kvStoreType === 'etcd') {
    services.push({ key: 'KVStoreservice', logoSlug: 'etcd', label: t('workspace.services.infra.etcd.label'), description: t('workspace.services.infra.etcd.description') });
  }

  const graphSlug = deployment?.graphDbType === 'neo4j' ? 'neo4j' : 'arangodb';
  services.push({ key: 'graphDb', logoSlug: graphSlug, label: t('workspace.services.infra.graphDb.label'), description: t('workspace.services.infra.graphDb.description') });

  const vectorSlug = deployment?.vectorDbType || 'qdrant';
  services.push({ key: 'vectorDb', logoSlug: vectorSlug, label: t('workspace.services.infra.vectorDb.label'), description: t('workspace.services.infra.vectorDb.description') });

  return services;
}
