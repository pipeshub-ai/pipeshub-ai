'use client';

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { McpServersApi } from '../../../workspace/mcp-servers/api';
import { isOfferedForNewServers, offeredForPersonalServers } from '../../../workspace/mcp-servers/catalog-replacement';
import { McpCatalogDetails } from '../../../workspace/mcp-servers/components/mcp-catalog-details';
import { McpDiscover } from '../../../workspace/mcp-servers/components/mcp-discover';
import { McpServerIcon } from '../../../workspace/mcp-servers/components/mcp-server-icon';
import { useCatalogSearch } from '../../../workspace/mcp-servers/hooks/use-catalog-search';
import { McpInstanceConfigPanel } from '../../../workspace/mcp-servers/team/components/mcp-instance-config-panel';
import type { McpMyServerEntry, McpServerTemplate } from '../../../workspace/mcp-servers/types';

const NO_TEMPLATES: McpServerTemplate[] = [];
// Enough to answer a search in the palette; Browse shows the rest.
const FALLBACK_LIMIT = 4;

interface AgentBuilderMcpDiscoveryProps {
  /** The palette search; catalog servers that match it are offered below the list. */
  search: string;
  mcpServers: McpMyServerEntry[];
  /** Admins add org servers; everyone else adds their own. */
  isAdmin: boolean;
  /** Shared with the org (or a service account): only org servers can be attached. */
  agentShared: boolean;
  refreshMcpServers: () => Promise<void>;
  onNotify: (message: string) => void;
  /** A server was added: show the full list, so the new server and its sign-in are in view. */
  onServerAdded?: () => void;
}

/**
 * Finding a server from the agent builder: catalog matches for the palette search, and Browse,
 * which opens the catalog over the canvas so an unsaved agent stays put.
 */
export function AgentBuilderMcpDiscovery({
  search,
  mcpServers,
  isAdmin,
  agentShared,
  refreshMcpServers,
  onNotify,
  onServerAdded,
}: AgentBuilderMcpDiscoveryProps) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<McpServerTemplate[] | null>(null);
  // Kept apart from an empty catalog, so a failed load can be asked again.
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [customStdioAllowed, setCustomStdioAllowed] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [details, setDetails] = useState<McpServerTemplate | null>(null);
  // Browse is a modal that would keep focus away from the details panel, so it closes while
  // the details are open and comes back when they close.
  const [detailsFromBrowse, setDetailsFromBrowse] = useState(false);
  const [setup, setSetup] = useState<McpServerTemplate | null>(null);
  // A member's servers are personal, which an agent shared with the org can't use.
  const canAdd = isAdmin || !agentShared;
  const needCatalog = browseOpen || search.trim() !== '';

  useEffect(() => {
    if (!needCatalog || catalog || catalogFailed) return;
    let cancelled = false;
    McpServersApi.getCatalog({ limit: 200 })
      .then((res) => {
        if (cancelled) return;
        setCatalog(isAdmin ? res.templates.filter(isOfferedForNewServers) : offeredForPersonalServers(res.templates));
        setCustomStdioAllowed(Boolean(res.customStdioAllowed));
      })
      .catch(() => {
        if (!cancelled) setCatalogFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [needCatalog, catalog, catalogFailed, isAdmin]);

  const { results } = useCatalogSearch(search, catalog ?? NO_TEMPLATES);
  const fromCatalog = useMemo(() => {
    const added = new Set(mcpServers.map((s) => s.typeId).filter(Boolean));
    return (results ?? []).filter((tpl) => !added.has(tpl.typeId)).slice(0, FALLBACK_LIMIT);
  }, [results, mcpServers]);

  const openBrowse = () => {
    setCatalogFailed(false);
    setBrowseOpen(true);
  };

  const openDetails = (tpl: McpServerTemplate, fromBrowse: boolean) => {
    setBrowseOpen(false);
    setDetailsFromBrowse(fromBrowse);
    setDetails(tpl);
  };

  const closeDetails = () => {
    setDetails(null);
    if (detailsFromBrowse) openBrowse();
    setDetailsFromBrowse(false);
  };

  const startSetup = (tpl: McpServerTemplate) => {
    setBrowseOpen(false);
    setDetails(null);
    setDetailsFromBrowse(false);
    setSetup(tpl);
  };

  return (
    <Flex direction="column" gap="1" data-testid="mcp-builder-discovery">
      {fromCatalog.length > 0 && (
        <Flex direction="column" gap="1" data-testid="mcp-builder-from-catalog">
          <Text
            size="1"
            weight="bold"
            style={{
              color: 'var(--gray-11)',
              padding: '6px 8px 2px',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
            }}
          >
            {t('workspace.mcpServers.discover.fromCatalog')}
          </Text>
          {fromCatalog.map((tpl) => (
            <Flex key={tpl.typeId} align="center" gap="2" style={{ padding: '4px 8px' }}>
              <McpServerIcon typeId={tpl.typeId} name={tpl.displayName} size={16} color="var(--gray-11)" />
              <Flex direction="column" style={{ minWidth: 0, flex: 1 }}>
                <Text size="1" weight="medium" truncate style={{ color: 'var(--gray-12)' }}>
                  {tpl.displayName}
                </Text>
                {tpl.matchedCapability && (
                  <Text size="1" truncate style={{ color: 'var(--accent-11)' }}>
                    {tpl.matchedCapability}
                  </Text>
                )}
              </Flex>
              <Button
                size="1"
                variant="soft"
                color="gray"
                onClick={() => openDetails(tpl, false)}
                style={{ cursor: 'pointer' }}
              >
                {canAdd ? t('workspace.mcpServers.discover.add') : t('workspace.mcpServers.discover.view')}
              </Button>
            </Flex>
          ))}
          {!canAdd && (
            <Text size="1" style={{ color: 'var(--gray-11)', padding: '2px 8px' }}>
              {t('workspace.mcpServers.discover.askAdmin')}
            </Text>
          )}
        </Flex>
      )}

      <Button
        size="1"
        variant="outline"
        color="gray"
        onClick={openBrowse}
        style={{ cursor: 'pointer', margin: '6px 8px 0' }}
      >
        <MaterialIcon name="explore" size={15} color="currentColor" />
        {t('workspace.mcpServers.discover.browse')}
      </Button>

      <Dialog.Root open={browseOpen} onOpenChange={setBrowseOpen}>
        <Dialog.Content
          maxWidth="1040px"
          style={{
            height: 'min(760px, calc(100vh - 48px))',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-3)',
          }}
        >
          <Flex align="center" justify="between" gap="3">
            <Dialog.Title size="4" mb="0">
              {t('workspace.mcpServers.discover.browse')}
            </Dialog.Title>
            <Dialog.Close>
              <IconButton variant="ghost" color="gray" aria-label={t('common.close')}>
                <MaterialIcon name="close" size={18} color="currentColor" />
              </IconButton>
            </Dialog.Close>
          </Flex>
          <Dialog.Description size="2" style={{ color: 'var(--gray-11)' }}>
            {canAdd ? t('workspace.mcpServers.discover.browseHint') : t('workspace.mcpServers.discover.askAdmin')}
          </Dialog.Description>
          <Flex direction="column" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
            {catalogFailed ? (
              <Flex direction="column" align="start" gap="2">
                <Text size="2" style={{ color: 'var(--gray-11)' }}>
                  {t('workspace.mcpServers.discover.catalogLoadFailed')}
                </Text>
                <Button size="1" variant="soft" color="gray" onClick={() => setCatalogFailed(false)}>
                  {t('action.tryAgain')}
                </Button>
              </Flex>
            ) : catalog === null ? (
              <Text size="2" style={{ color: 'var(--gray-11)' }}>
                {t('workspace.mcpServers.loading')}
              </Text>
            ) : (
              <McpDiscover
                catalog={catalog}
                instances={mcpServers}
                onOpen={(tpl) => openDetails(tpl, true)}
                onAdd={canAdd ? startSetup : (tpl) => openDetails(tpl, true)}
              />
            )}
          </Flex>
        </Dialog.Content>
      </Dialog.Root>

      <McpCatalogDetails
        template={details}
        addedCount={details ? mcpServers.filter((s) => s.typeId === details.typeId).length : 0}
        isAdmin={isAdmin}
        addBlockedNote={
          canAdd || !details
            ? undefined
            : t('workspace.mcpServers.discover.sharedAgentNote', { name: details.displayName })
        }
        onClose={closeDetails}
        onAdd={startSetup}
      />

      {/* Mounted only while adding, so the builder doesn't carry the setup form otherwise. */}
      {setup && (
        <McpInstanceConfigPanel
          scope={isAdmin ? 'org' : 'personal'}
          state={{ open: true, mode: 'create', editingInstance: null, prefillTemplate: setup }}
          templates={catalog ?? NO_TEMPLATES}
          instances={mcpServers}
          customStdioAllowed={isAdmin && customStdioAllowed}
          onOpenChange={(open) => {
            if (!open) setSetup(null);
          }}
          onSaved={({ created }) => {
            const name = setup?.displayName ?? '';
            setSetup(null);
            void refreshMcpServers();
            if (created) {
              onServerAdded?.();
              onNotify(t('workspace.mcpServers.discover.addedSignIn', { name }));
            }
          }}
        />
      )}
    </Flex>
  );
}
