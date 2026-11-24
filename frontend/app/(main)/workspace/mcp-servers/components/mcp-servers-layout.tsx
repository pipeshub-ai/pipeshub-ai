'use client';

import { useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Heading, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { lower } from '@/lib/utils/safe';
import { pagePaddingX } from '@/app/components/ui/responsive';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from '@/app/(main)/workspace/components/settings-page-container';
import type { McpMyServerEntry, McpServerTemplate } from '../types';
import { McpCatalogDetails } from './mcp-catalog-details';
import { McpDiscover } from './mcp-discover';
import { McpPopularRow } from './mcp-popular-row';
import { McpServerList } from './mcp-server-list';

interface McpServersLayoutProps {
  title: string;
  subtitle: string;
  /** Add server, Refresh and the like, beside the search. */
  actions: ReactNode;
  isLoading: boolean;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  instances: McpMyServerEntry[];
  /** The whole catalog, to name the service each server was made from. */
  templates: McpServerTemplate[];
  /** The catalog entries this person may add. */
  catalog: McpServerTemplate[];
  catalogDescription: string;
  emptyTitle: string;
  emptyDescription: string;
  isAdmin: boolean;
  busyInstanceId: string | null;
  onOpen: (instance: McpMyServerEntry) => void;
  onConnect: (instance: McpMyServerEntry) => void;
  onReconnect: (instance: McpMyServerEntry) => void;
  onEdit?: (instance: McpMyServerEntry) => void;
  onAdd: (template: McpServerTemplate) => void;
  footer?: ReactNode;
}

/** The servers this person has, with their status, then the catalog to discover more in. */
export function McpServersLayout({
  title,
  subtitle,
  actions,
  isLoading,
  searchQuery,
  onSearchChange,
  instances,
  templates,
  catalog,
  catalogDescription,
  emptyTitle,
  emptyDescription,
  isAdmin,
  busyInstanceId,
  onOpen,
  onConnect,
  onReconnect,
  onEdit,
  onAdd,
  footer,
}: McpServersLayoutProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const q = searchQuery.trim().toLowerCase();
  const [details, setDetails] = useState<McpServerTemplate | null>(null);

  const shownServers = useMemo(
    () => (q ? instances.filter((i) => lower(i.name).includes(q) || lower(i.description).includes(q)) : instances),
    [instances, q]
  );

  return (
    <Flex
      direction="column"
      gap="6"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : PAGE_PADDING_TOP,
        paddingBottom: 64,
        paddingInline: pagePaddingX(100),
        overflowY: 'auto',
        background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
      }}
    >
      <Flex direction="column" gap="4" style={{ width: '100%' }}>
        <Flex justify="between" align="start" wrap="wrap" gap="3" style={{ width: '100%' }}>
          <Flex direction="column" gap="2" style={{ flex: '1 1 240px', minWidth: 0 }}>
            <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {title}
            </Heading>
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {subtitle}
            </Text>
          </Flex>
          <Flex align="center" gap="2" wrap="wrap" style={{ width: isMobile ? '100%' : undefined }}>
            {instances.length > 0 && (
              <Box width={{ initial: '100%', sm: '224px' }} flexShrink="0">
                <TextField.Root
                  size="2"
                  placeholder={t('workspace.mcpServers.searchYourServers')}
                  aria-label={t('workspace.mcpServers.searchYourServers')}
                  value={searchQuery}
                  onChange={(e) => onSearchChange(e.target.value)}
                  style={{ width: '100%' }}
                >
                  <TextField.Slot>
                    <MaterialIcon name="search" size={16} color="var(--gray-9)" />
                  </TextField.Slot>
                </TextField.Root>
              </Box>
            )}
            {actions}
          </Flex>
        </Flex>
      </Flex>

      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.mcpServers.loading')} />
        </Flex>
      ) : (
        <>
          {instances.length === 0 ? (
            <Flex
              direction="column"
              align="center"
              gap="2"
              style={{
                width: '100%',
                padding: 'var(--space-6) var(--space-4)',
                border: '1px dashed var(--olive-5)',
                borderRadius: 'var(--radius-3)',
                textAlign: 'center',
              }}
            >
              <MaterialIcon name="hub" size={36} color="var(--accent-9)" />
              <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {emptyTitle}
              </Text>
              <Text size="2" style={{ color: 'var(--gray-11)', maxWidth: 520 }}>
                {emptyDescription}
              </Text>
            </Flex>
          ) : shownServers.length > 0 ? (
            <McpServerList
              instances={shownServers}
              templates={templates}
              isAdmin={isAdmin}
              busyInstanceId={busyInstanceId}
              onOpen={onOpen}
              onConnect={onConnect}
              onReconnect={onReconnect}
              onEdit={onEdit}
            />
          ) : (
            <Text size="2" align="center" style={{ color: 'var(--gray-10)', padding: 'var(--space-6)' }}>
              {t('workspace.mcpServers.noResults')}
            </Text>
          )}

          {catalog.length > 0 && (
            <McpDiscover
              catalog={catalog}
              instances={instances}
              description={catalogDescription}
              highlights={<McpPopularRow catalog={catalog} onOpen={setDetails} />}
              onOpen={setDetails}
              onAdd={onAdd}
            />
          )}
        </>
      )}

      {!isLoading && footer}

      <McpCatalogDetails
        template={details}
        addedCount={details ? instances.filter((i) => i.typeId === details.typeId).length : 0}
        isAdmin={isAdmin}
        onClose={() => setDetails(null)}
        onAdd={(tpl) => {
          setDetails(null);
          onAdd(tpl);
        }}
      />
    </Flex>
  );
}
