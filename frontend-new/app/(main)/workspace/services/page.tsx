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
  selectLastChecked,
  type ServiceStatus,
} from '@/lib/store/services-health-store';
import { apiClient } from '@/lib/api/axios-instance';

// ========================================
// Service metadata
// ========================================

interface ServiceMeta {
  key: string;
  icon: string;
  label: string;
  description: string;
}

interface Deployment {
  kvStoreType: string;
  messageBrokerType: string;
  graphDbType: string;
}

function buildInfraServices(deployment: Deployment | null): ServiceMeta[] {
  const services: ServiceMeta[] = [];

  if (deployment?.kvStoreType === 'redis') {
    services.push({ key: 'redis', icon: 'memory', label: 'Redis', description: 'Caching, rate limiting, and configuration' });
  } else {
    services.push({ key: 'redis', icon: 'memory', label: 'Redis', description: 'Caching and rate limiting' });
  }

  services.push({ key: 'mongodb', icon: 'storage', label: 'MongoDB', description: 'Sessions and user metadata' });
  services.push({ key: 'messageBroker', icon: 'swap_horiz', label: 'Message Broker', description: 'Distributed event streaming' });

  if (deployment?.kvStoreType === 'etcd') {
    services.push({ key: 'KVStoreservice', icon: 'settings_ethernet', label: 'etcd', description: 'Distributed configuration' });
  }

  services.push({ key: 'graphDb', icon: 'account_tree', label: 'Graph Database', description: 'Knowledge graphs' });
  services.push({ key: 'vectorDb', icon: 'scatter_plot', label: 'Vector Database', description: 'Semantic search embeddings' });

  return services;
}

const APP_SERVICES: ServiceMeta[] = [
  { key: 'query', icon: 'search', label: 'Query Service', description: 'RAG, semantic search, and LLM integration' },
  { key: 'connector', icon: 'hub', label: 'Connector Service', description: 'OAuth, token refresh, and data source integrations' },
  { key: 'indexing', icon: 'inventory_2', label: 'Indexing Service', description: 'Document parsing, embedding generation, and chunking' },
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
        <MaterialIcon name={meta.icon} size={18} color="var(--slate-11)" />
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

  const infraServiceList = useMemo(() => buildInfraServices(localDeployment), [localDeployment]);

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
          title: allHealthy ? 'All services are healthy' : 'Some services are unhealthy',
        });
      }
    } catch {
      if (showToast) {
        addToast({ variant: 'error', title: 'Failed to check services health' });
      }
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, [addToast]);

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
        <LottieLoader variant="loader" size={48} showLabel label="Loading services status…" />
      </Flex>
    );
  }

  const lastCheckedLabel = localLastChecked
    ? `Last checked: ${new Date(localLastChecked).toLocaleTimeString()}`
    : null;

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
