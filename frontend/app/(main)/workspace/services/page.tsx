'use client';

import React, { useEffect, useCallback, useState, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  Box,
  Flex,
  Text,
  Heading,
  Badge,
  Button,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import {
  useServicesHealthStore,
  selectInfraServices,
  selectAppServices,
  selectLastChecked,
  type ServiceStatus,
} from '@/lib/store/services-health-store';
import { apiClient } from '@/lib/api/axios-instance';

// ========================================
// Service metadata
// ========================================

interface ServiceMeta {
  key: string;
  icon?: string;
  logoSlug?: string;
  label: string;
  description: string;
}

interface Deployment {
  kvStoreType: string;
  messageBrokerType: string;
  graphDbType: string;
  vectorDbType?: string;
}

const LOGO_SIZE = 22;

function buildInfraServices(deployment: Deployment | null, t: TFunction): ServiceMeta[] {
  const services: ServiceMeta[] = [];

  const redisDesc = deployment?.kvStoreType === 'redis'
    ? t('workspace.services.infra.redis.descriptionWithKvStore')
    : t('workspace.services.infra.redis.description');
  services.push({ key: 'redis', logoSlug: 'redis', label: t('workspace.services.infra.redis.label'), description: redisDesc });

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

function buildAppServices(t: TFunction): ServiceMeta[] {
  return [
    { key: 'query', icon: 'search', label: t('workspace.services.app.query.label'), description: t('workspace.services.app.query.description') },
    { key: 'connector', icon: 'hub', label: t('workspace.services.app.connector.label'), description: t('workspace.services.app.connector.description') },
    { key: 'indexing', icon: 'dataset', label: t('workspace.services.app.indexing.label'), description: t('workspace.services.app.indexing.description') },
    { key: 'docling', icon: 'description', label: t('workspace.services.app.docling.label'), description: t('workspace.services.app.docling.description') },
  ];
}

// ========================================
// Sub-components
// ========================================

function ServiceStatusBadge({ status }: { status: ServiceStatus | undefined }) {
  const { t } = useTranslation();
  if (!status || status === 'unknown') {
    return (
      <Badge color="gray" variant="soft" size="1" style={{ flexShrink: 0 }}>
        {t('workspace.services.statusUnknown')}
      </Badge>
    );
  }
  if (status === 'healthy') {
    return (
      <Badge color="green" variant="soft" size="1" style={{ flexShrink: 0 }}>
        {t('workspace.services.statusHealthy')}
      </Badge>
    );
  }
  return (
    <Badge color="red" variant="soft" size="1" style={{ flexShrink: 0 }}>
      {t('workspace.services.statusUnhealthy')}
    </Badge>
  );
}

function ServiceRow({
  meta,
  status,
  displayName,
}: {
  meta: ServiceMeta;
  status: ServiceStatus | undefined;
  displayName?: string;
}) {
  return (
    <Flex
      align="center"
      gap="3"
      style={{
        padding: '12px 14px',
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--slate-1)',
      }}
    >
      {/* Icon box */}
      <Flex
        align="center"
        justify="center"
        style={{
          width: 36,
          height: 36,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--slate-3)',
          flexShrink: 0,
        }}
      >
        {meta.logoSlug ? (
          <Box
            role="img"
            aria-label={`${meta.label} logo`}
            style={{
              width: LOGO_SIZE,
              height: LOGO_SIZE,
              backgroundImage: `url(/icons/logos/${meta.logoSlug}.svg)`,
              backgroundRepeat: 'no-repeat',
              backgroundPosition: 'center',
              backgroundSize: 'contain',
            }}
          />
        ) : meta.icon ? (
          <MaterialIcon name={meta.icon} size={18} color="var(--slate-11)" />
        ) : null}
      </Flex>

      {/* Label + description */}
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
          {displayName || meta.label}
        </Text>
        <Text
          size="1"
          style={{
            color: 'var(--slate-10)',
            display: 'block',
            marginTop: 2,
            fontWeight: 300,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {meta.description}
        </Text>
      </Box>

      {/* Status badge */}
      <ServiceStatusBadge status={status} />
    </Flex>
  );
}

// ========================================
// Page
// ========================================

export default function ServicesPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const addToast = useToastStore((s) => s.addToast);

  const infraServices = useServicesHealthStore(selectInfraServices);
  const appServices = useServicesHealthStore(selectAppServices);
  const lastChecked = useServicesHealthStore(selectLastChecked);

  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [localInfra, setLocalInfra] = useState(infraServices);
  const [localApp, setLocalApp] = useState(appServices);
  const [localServiceNames, setLocalServiceNames] = useState<Record<string, string> | null>(null);
  const [localDeployment, setLocalDeployment] = useState<Deployment | null>(null);
  const [localLastChecked, setLocalLastChecked] = useState(lastChecked);

  const infraServiceList = useMemo(() => buildInfraServices(localDeployment, t), [localDeployment, t]);
  const appServiceList = useMemo(() => buildAppServices(t), [t]);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  const fetchHealth = useCallback(async (showToast = false) => {
    try {
      const [infraResp, servicesResp] = await Promise.allSettled([
        apiClient.get('/api/v1/health', { suppressErrorToast: true }),
        apiClient.get('/api/v1/health/services', { suppressErrorToast: true }),
      ]);

      const infraData = infraResp.status === 'fulfilled' ? infraResp.value.data : null;
      const servicesData = servicesResp.status === 'fulfilled' ? servicesResp.value.data : null;

      setLocalInfra(infraData?.services ?? null);
      setLocalApp(servicesData?.services ?? null);
      setLocalServiceNames(infraData?.serviceNames ?? null);
      setLocalDeployment(infraData?.deployment ?? null);
      setLocalLastChecked(Date.now());

      if (showToast) {
        const allHealthy = infraData?.status === 'healthy' && servicesData?.status === 'healthy';
        addToast({
          variant: allHealthy ? 'success' : 'warning',
          title: allHealthy ? t('workspace.services.allHealthy') : t('workspace.services.someUnhealthy'),
        });
      }
    } catch {
      if (showToast) {
        addToast({ variant: 'error', title: t('workspace.services.fetchFailed') });
      }
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, [addToast, t]);

  useEffect(() => {
    if (!isProfileInitialized || isAdmin === false) return;
    fetchHealth();
  }, [isProfileInitialized, isAdmin, fetchHealth]);

  const handleRefresh = useCallback(() => {
    setIsRefreshing(true);
    fetchHealth(true);
  }, [fetchHealth]);

  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel label={t('workspace.services.loading')} />
      </Flex>
    );
  }

  const lastCheckedLabel = localLastChecked
    ? t('workspace.services.lastChecked', { time: new Date(localLastChecked).toLocaleTimeString() })
    : null;

  return (
    <Box style={{ height: '100%', overflowY: 'auto', background: 'linear-gradient(180deg, var(--olive-2) 0%, var(--olive-1) 100%)' }}>
      <Box style={{ padding: '64px 100px 80px' }}>
        {/* ── Page header ── */}
        <Flex align="start" justify="between" style={{ marginBottom: 24 }}>
          <Box>
            <Heading size="6" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.services.pageTitle')}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
              {t('workspace.services.pageSubtitle')}
            </Text>
          </Box>

          <Flex align="center" gap="3">
            {lastCheckedLabel && (
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                {lastCheckedLabel}
              </Text>
            )}
            <Button
              variant="outline"
              color="gray"
              size="2"
              onClick={handleRefresh}
              disabled={isRefreshing}
              style={{ cursor: isRefreshing ? 'wait' : 'pointer', flexShrink: 0, gap: 6 }}
            >
              <span
                className="material-icons-outlined"
                style={{
                  fontSize: 15,
                  animation: isRefreshing ? 'spin 1s linear infinite' : undefined,
                }}
              >
                refresh
              </span>
              {isRefreshing ? t('workspace.services.checking') : t('workspace.services.checkNow')}
            </Button>
          </Flex>
        </Flex>

        {/* ── Infrastructure Services section ── */}
        <Flex
          direction="column"
          style={{
            border: '1px solid var(--slate-5)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-2)',
            marginBottom: 20,
          }}
        >
          {/* Section header */}
          <Box style={{ padding: '14px 16px', borderBottom: '1px solid var(--slate-5)' }}>
            <Text size="3" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
              {t('workspace.services.infraSection')}
            </Text>
            <Text
              size="1"
              style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
            >
              {t('workspace.services.infraSubtitle')}
            </Text>
          </Box>

          {/* Service rows */}
          <Flex direction="column" gap="2" style={{ padding: '12px 14px' }}>
            {infraServiceList.map((meta) => (
              <ServiceRow
                key={meta.key}
                meta={meta}
                status={localInfra?.[meta.key as keyof typeof localInfra]}
                displayName={localServiceNames?.[meta.key]}
              />
            ))}
          </Flex>
        </Flex>

        {/* ── Application Services section ── */}
        <Flex
          direction="column"
          style={{
            border: '1px solid var(--slate-5)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-2)',
            marginBottom: 20,
          }}
        >
          {/* Section header */}
          <Box style={{ padding: '14px 16px', borderBottom: '1px solid var(--slate-5)' }}>
            <Text size="3" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
              {t('workspace.services.appSection')}
            </Text>
            <Text
              size="1"
              style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
            >
              {t('workspace.services.appSubtitle')}
            </Text>
          </Box>

          {/* Service rows */}
          <Flex direction="column" gap="2" style={{ padding: '12px 14px' }}>
            {appServiceList.map((meta) => (
              <ServiceRow
                key={meta.key}
                meta={meta}
                status={localApp?.[meta.key as keyof typeof localApp]}
              />
            ))}
          </Flex>
        </Flex>

        {/* ── Info callout ── */}
        <Flex
          align="start"
          gap="3"
          style={{
            backgroundColor: 'var(--accent-2)',
            border: '1px solid var(--accent-6)',
            borderRadius: 'var(--radius-1)',
            padding: '12px 16px',
          }}
        >
          <Box style={{ flexShrink: 0, marginTop: 2 }}>
            <MaterialIcon name="info" size={16} color="var(--accent-9)" />
          </Box>
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.services.policyTitle')}
            </Text>
            <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', fontWeight: 300 }}>
              {t('workspace.services.policyDescription')}
            </Text>
          </Flex>
        </Flex>
      </Box>
    </Box>
  );
}
