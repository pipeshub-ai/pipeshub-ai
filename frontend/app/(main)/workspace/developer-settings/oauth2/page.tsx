'use client';

import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { Flex, Grid, Text, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useUserStore, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useBreakpoint } from '@/lib/hooks/use-breakpoint';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { EntityEmptyState, EntityPagination } from '../../components';
import { Oauth2Api } from './api';
import type { OAuthClient, OAuthClientsPagination } from './types';
import { Oauth2PageHeader } from './components/oauth2-page-header';
import { CreateOAuthApplicationPanel } from './components/create-oauth-application-panel';
import { ManageOAuthApplicationPanel } from './components/manage-oauth-application-panel';
import { OAuthApplicationCard } from './components/oauth-application-card';

function Oauth2PageContent() {
  const { t } = useTranslation();
  const breakpoint = useBreakpoint();
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const [clients, setClients] = useState<OAuthClient[]>([]);
  const [pagination, setPagination] = useState<OAuthClientsPagination | null>(null);
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(20);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [createPanelOpen, setCreatePanelOpen] = useState(false);
  const [managePanelOpen, setManagePanelOpen] = useState(false);
  const [manageClientId, setManageClientId] = useState<string | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setPage(1);
      setDebouncedSearchQuery(searchQuery.trim());
    }, 400);

    return () => {
      window.clearTimeout(handle);
    };
  }, [searchQuery]);

  const fetchClients = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await Oauth2Api.listOAuthClients({
        page,
        limit,
        search: debouncedSearchQuery || undefined,
      });
      setClients(data.data ?? []);
      setPagination(
        data.pagination ?? {
          page,
          limit,
          total: data.data?.length ?? 0,
          totalPages: 1,
        }
      );
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : t('workspace.oauth2.errorGeneric');
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [page, limit, debouncedSearchQuery, t]);

  useEffect(() => {
    if (!isProfileInitialized) return;
    void fetchClients();
  }, [isProfileInitialized, fetchClients]);

  const handleNewApplication = useCallback(() => {
    setCreatePanelOpen(true);
  }, []);

  const handleLimitChange = useCallback((next: number) => {
    setPage(1);
    setLimit(next);
  }, []);

  const handleManageClient = useCallback((client: OAuthClient) => {
    setManageClientId(client.id);
    setManagePanelOpen(true);
  }, []);

  const handleManagePanelOpenChange = useCallback((open: boolean) => {
    setManagePanelOpen(open);
    if (!open) {
      setManageClientId(null);
    }
  }, []);

  const hasSearchQuery = debouncedSearchQuery.length > 0;
  const noSearchResults =
    !isLoading &&
    !error &&
    pagination !== null &&
    pagination.total === 0 &&
    hasSearchQuery;

  if (!isProfileInitialized) {
    return null;
  }

  const pagePaddingX = 'clamp(var(--space-4), 4vw, 40px)';
  const totalCount = pagination?.total ?? 0;

  const isEmpty =
    !isLoading && !error && pagination !== null && pagination.total === 0 && !hasSearchQuery;

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        paddingLeft: pagePaddingX,
        paddingRight: pagePaddingX,
        boxSizing: 'border-box',
        minWidth: 0,
      }}
    >
      <Oauth2PageHeader
        title={t('workspace.oauth2.title')}
        subtitle={t('workspace.oauth2.subtitle')}
        searchPlaceholder={t('workspace.oauth2.searchPlaceholder')}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        newApplicationLabel={t('workspace.oauth2.newApplication')}
        onNewApplication={handleNewApplication}
        docsOpenLabel={t('workspace.oauth2.docsOpenLabel')}
        breakpoint={breakpoint}
      />

      <Flex
        direction="column"
        style={{
          flex: 1,
          minHeight: 0,
          width: '100%',
          minWidth: 0,
        }}
      >
        {isLoading && (
          <Flex align="center" justify="center" style={{ flex: 1, padding: 'var(--space-6)' }}>
            <LottieLoader variant="loader" size={48} showLabel />
          </Flex>
        )}

        {!isLoading && error && (
          <Flex
            direction="column"
            align="center"
            justify="center"
            gap="3"
            style={{ flex: 1, padding: 'var(--space-6)' }}
          >
            <Text size="2" style={{ color: 'var(--red-11)', textAlign: 'center' }}>
              {error}
            </Text>
            <Button size="2" variant="soft" onClick={() => void fetchClients()}>
              {t('workspace.oauth2.retry')}
            </Button>
          </Flex>
        )}

        {!isLoading && !error && isEmpty && (
          <Flex
            direction="column"
            style={{
              flex: 1,
              minHeight: 'min(60vh, 560px)',
              width: '100%',
              minWidth: 0,
              paddingLeft: 'var(--space-2)',
              paddingRight: 'var(--space-2)',
              boxSizing: 'border-box',
            }}
          >
            <EntityEmptyState
              icon="vpn_key"
              title={t('workspace.oauth2.emptyTitle')}
              description={t('workspace.oauth2.emptyDescription')}
              ctaLabel={t('workspace.oauth2.newApplication')}
              ctaIcon="add"
              onCtaClick={handleNewApplication}
            />
          </Flex>
        )}

        {!isLoading && !error && !isEmpty && (
          <Flex
            direction="column"
            style={{
              flex: 1,
              minHeight: 0,
              overflow: 'hidden',
            }}
          >
            <Flex
              direction="column"
              style={{
                flex: 1,
                minHeight: 0,
                overflow: 'auto',
                paddingLeft: 'var(--space-2)',
                paddingRight: 'var(--space-2)',
                paddingTop: 'var(--space-4)',
                paddingBottom: 'var(--space-4)',
                boxSizing: 'border-box',
              }}
            >
              {noSearchResults ? (
                <Flex align="center" justify="center" style={{ flex: 1, minHeight: 200 }}>
                  <Text size="2" style={{ color: 'var(--slate-11)', textAlign: 'center' }}>
                    {t('workspace.oauth2.noSearchResults')}
                  </Text>
                </Flex>
              ) : (
                <Grid
                  columns={{ initial: '2', md: '3', lg: '3' }}
                  gap="4"
                  style={{ width: '100%' }}
                >
                  {clients.map((client) => (
                    <OAuthApplicationCard
                      key={client.id}
                      client={client}
                      onManage={handleManageClient}
                    />
                  ))}
                </Grid>
              )}
            </Flex>

            <EntityPagination
              page={page}
              limit={limit}
              totalCount={totalCount}
              onPageChange={setPage}
              onLimitChange={handleLimitChange}
            />
          </Flex>
        )}
      </Flex>

      <CreateOAuthApplicationPanel
        open={createPanelOpen}
        onOpenChange={setCreatePanelOpen}
        onCreated={() => void fetchClients()}
        onOpenManage={(applicationId) => {
          setManageClientId(applicationId);
          setManagePanelOpen(true);
        }}
      />

      <ManageOAuthApplicationPanel
        open={managePanelOpen}
        onOpenChange={handleManagePanelOpenChange}
        clientId={manageClientId}
        onSaved={() => void fetchClients()}
      />
    </Flex>
  );
}

export default function Oauth2Page() {
  return (
    <Suspense fallback={null}>
      <Oauth2PageContent />
    </Suspense>
  );
}
