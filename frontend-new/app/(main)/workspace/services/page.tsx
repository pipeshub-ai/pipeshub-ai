'use client';

import React, { useEffect, useCallback, useState, useMemo } from 'react';
import { useRouter } from 'next/navigation';
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
  selectInfraServiceNames,
  selectDeployment,
  selectLastChecked,
  type ServiceStatus,
  type Deployment,
} from '@/lib/store/services-health-store';

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

const LOGO_SIZE = 22;

function buildInfraServices(deployment: Deployment | null): ServiceMeta[] {
  const services: ServiceMeta[] = [];

  if (deployment?.kvStoreType === 'redis') {
    services.push({ key: 'redis', logoSlug: 'redis', label: 'Redis', description: 'Caching, rate limiting, and configuration' });
  } else {
    services.push({ key: 'redis', logoSlug: 'redis', label: 'Redis', description: 'Caching and rate limiting' });
  }

  services.push({ key: 'mongodb', logoSlug: 'mongodb', label: 'MongoDB', description: 'Sessions and user metadata' });

  const brokerSlug = deployment?.messageBrokerType === 'redis' ? 'redis' : 'apachekafka';
  services.push({ key: 'messageBroker', logoSlug: brokerSlug, label: 'Message Broker', description: 'Distributed event streaming' });

  if (deployment?.kvStoreType === 'etcd') {
    services.push({ key: 'KVStoreservice', logoSlug: 'etcd', label: 'etcd', description: 'Distributed configuration' });
  }

  const graphSlug = deployment?.graphDbType === 'neo4j' ? 'neo4j' : 'arangodb';
  services.push({ key: 'graphDb', logoSlug: graphSlug, label: 'Graph Database', description: 'Knowledge graphs' });

  const vectorSlug = deployment?.vectorDbType || 'qdrant';
  services.push({ key: 'vectorDb', logoSlug: vectorSlug, label: 'Vector Database', description: 'Semantic search embeddings' });

  return services;
}

const APP_SERVICES: ServiceMeta[] = [
  { key: 'query', icon: 'search', label: 'Query Service', description: 'RAG, semantic search, and LLM integration' },
  { key: 'connector', icon: 'hub', label: 'Connector Service', description: 'OAuth, token refresh, and data source integrations' },
  { key: 'indexing', icon: 'dataset', label: 'Indexing Service', description: 'Document parsing, embedding generation, and chunking' },
  { key: 'docling', icon: 'description', label: 'Docling Service', description: 'Advanced document parsing and OCR' },
];

// ========================================
// Sub-components
// ========================================

function ServiceStatusBadge({ status }: { status: ServiceStatus | undefined }) {
  if (!status || status === 'unknown') {
    return (
      <Badge color="gray" variant="soft" size="1" style={{ flexShrink: 0 }}>
        Unknown
      </Badge>
    );
  }
  if (status === 'healthy') {
    return (
      <Badge color="green" variant="soft" size="1" style={{ flexShrink: 0 }}>
        Healthy
      </Badge>
    );
  }
  return (
    <Badge color="red" variant="soft" size="1" style={{ flexShrink: 0 }}>
      Unhealthy
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
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const addToast = useToastStore((s) => s.addToast);

  const checkHealth = useServicesHealthStore((s) => s.checkHealth);
  const infraServices = useServicesHealthStore(selectInfraServices);
  const appServices = useServicesHealthStore(selectAppServices);
  const infraServiceNames = useServicesHealthStore(selectInfraServiceNames);
  const deployment = useServicesHealthStore(selectDeployment);
  const lastChecked = useServicesHealthStore(selectLastChecked);

  const [isRefreshing, setIsRefreshing] = useState(false);

  const infraServiceList = useMemo(() => buildInfraServices(deployment), [deployment]);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const allHealthy = await checkHealth();
      addToast({
        variant: allHealthy ? 'success' : 'warning',
        title: allHealthy ? 'All services are healthy' : 'Some services are unhealthy',
      });
    } finally {
      setIsRefreshing(false);
    }
  }, [checkHealth, addToast]);

  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  if (lastChecked === null) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel label="Loading services status…" />
      </Flex>
    );
  }

  const lastCheckedLabel = `Last checked: ${new Date(lastChecked).toLocaleTimeString()}`;

  return (
    <Box style={{ height: '100%', overflowY: 'auto', background: 'linear-gradient(180deg, var(--olive-2) 0%, var(--olive-1) 100%)' }}>
      <Box style={{ padding: '64px 100px 80px' }}>
        {/* ── Page header ── */}
        <Flex align="start" justify="between" style={{ marginBottom: 24 }}>
          <Box>
            <Heading size="6" style={{ color: 'var(--slate-12)' }}>
              Services
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
              Monitor the health status of platform services
            </Text>
          </Box>

          <Flex align="center" gap="3">
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              {lastCheckedLabel}
            </Text>
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
              {isRefreshing ? 'Checking…' : 'Check Now'}
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
              Infrastructure Services
            </Text>
            <Text
              size="1"
              style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
            >
              Core infrastructure components powering the platform
            </Text>
          </Box>

          {/* Service rows */}
          <Flex direction="column" gap="2" style={{ padding: '12px 14px' }}>
            {infraServiceList.map((meta) => (
              <ServiceRow
                key={meta.key}
                meta={meta}
                status={infraServices?.[meta.key as keyof typeof infraServices]}
                displayName={infraServiceNames?.[meta.key]}
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
              Application Services
            </Text>
            <Text
              size="1"
              style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
            >
              Python microservices handling search, indexing, and document processing
            </Text>
          </Box>

          {/* Service rows */}
          <Flex direction="column" gap="2" style={{ padding: '12px 14px' }}>
            {APP_SERVICES.map((meta) => (
              <ServiceRow
                key={meta.key}
                meta={meta}
                status={appServices?.[meta.key as keyof typeof appServices]}
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
              Service Health Policy
            </Text>
            <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', fontWeight: 300 }}>
              Query and Connector services are critical — the application will wait for them before
              loading. Indexing and Docling services are non-blocking and only affect background
              processing.
            </Text>
          </Flex>
        </Flex>
      </Box>
    </Box>
  );
}
