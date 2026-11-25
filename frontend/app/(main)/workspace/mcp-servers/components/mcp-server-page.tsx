'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Heading, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { toast } from '@/lib/store/toast-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useCopyText } from '@/lib/hooks/use-copy-text';
import { useUserDirectoryEntry } from '@/lib/hooks/use-user-directory-entry';
import { pagePaddingX } from '@/app/components/ui/responsive';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from '@/app/(main)/workspace/components/settings-page-container';
import { isMcpInstanceReadOnly, McpInheritedCallout, McpInstanceBadges, useMcpInstanceAdminTab } from '@/config';
import { ConfirmationDialog } from '../../components';
import { McpStatusBadge, mcpStatusGroup, usesSharedCredential } from '../connection-state';
import { isOfferedForNewServers, replacementFor } from '../catalog-replacement';
import { useMcpServerTools } from '../hooks/use-mcp-server-tools';
import { McpServerActionsMenu, type McpMenuEntry } from '../team/components/mcp-panel-header';
import { McpToolsApprovalsTab } from '../team/components/mcp-tools-approvals-tab';
import { ruleToolsFromInfo } from '../tool-rules';
import {
  MCP_AUTH_MODE_LABELS,
  MCP_TRANSPORT_LABELS,
  isPersonalMcpInstance,
  type McpMyServerEntry,
  type McpServerTemplate,
} from '../types';
import { McpDisconnectDialog } from './mcp-disconnect-dialog';
import { McpServerIcon } from './mcp-server-icon';
import { McpDisabledBadge, isMcpInstanceDisabled } from './mcp-stdio-policy';
import { useToolRulesEditor } from './mcp-tool-rules-editor';


interface McpServerPageProps {
  instance: McpMyServerEntry;
  templates: McpServerTemplate[];
  /** Status as an administrator sees it (the team page). */
  isAdmin: boolean;
  /** The person sets their own approval rules here; on the team page company-wide rules are an
   * Enterprise Edition feature, so the tools are only listed. */
  rulesEditable: boolean;
  isBusy: boolean;
  onBack: () => void;
  onAuthenticate: () => void;
  onReauthenticate: () => void;
  onDisconnect: () => Promise<void> | void;
  /** Absent where this person can't change the server. */
  onEdit?: () => void;
  onDelete?: () => void;
  /** Just added: say what is left before it can be used, until the person dismisses it. */
  justAdded?: boolean;
  onDismissAdded?: () => void;
  /** Opened from a link to the server's admin section (`tab=hub`): scroll to it, where the build has one. */
  focusAdminSection?: boolean;
}

/** One server: how it is doing, what fixes it, and its tools. */
export function McpServerPage({
  instance,
  templates,
  isAdmin,
  rulesEditable,
  isBusy,
  onBack,
  onAuthenticate,
  onReauthenticate,
  onDisconnect,
  onEdit,
  onDelete,
  justAdded = false,
  onDismissAdded,
  focusAdminSection = false,
}: McpServerPageProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const { copy } = useCopyText();
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  const { toolsLoad, loadTools, connectionState } = useMcpServerTools(instance, { isAdmin });
  const ruleTools = useMemo(() => ruleToolsFromInfo(toolsLoad.status === 'ready' ? toolsLoad.tools : []), [toolsLoad]);
  const rulesEditor = useToolRulesEditor(instance._id, ruleTools, rulesEditable);

  const template = templates.find((tpl) => tpl.typeId === instance.typeId) ?? null;
  const replacedNotice =
    template && !isOfferedForNewServers(template)
      ? t('workspace.mcpServers.replacedNotice', {
          name: replacementFor(template, templates)?.displayName ?? template.replacedBy,
        })
      : null;
  const details = [
    template?.displayName ?? (instance.isCustom ? t('workspace.mcpServers.custom') : null),
    MCP_TRANSPORT_LABELS[instance.transport],
    MCP_AUTH_MODE_LABELS[instance.authMode],
    usesSharedCredential(instance) ? t('workspace.mcpServers.sharedAuth') : null,
  ].filter(Boolean);

  const creatorHidden = isMcpInstanceReadOnly(instance);
  const adminSection = useMcpInstanceAdminTab(
    isAdmin && !creatorHidden && !isPersonalMcpInstance(instance)
      ? { instanceId: instance._id, transport: instance.transport, insideOrganization: instance.insideOrganization === true }
      : null
  );
  const adminSectionRef = useRef<HTMLDivElement>(null);
  const hasAdminSection = adminSection !== null;
  useEffect(() => {
    if (focusAdminSection && hasAdminSection) adminSectionRef.current?.scrollIntoView?.({ block: 'start' });
  }, [focusAdminSection, hasAdminSection]);
  const creator = useUserDirectoryEntry(creatorHidden ? null : instance.createdBy);
  const creatorName = !creatorHidden && creator ? creator.name?.trim() || instance.createdBy : null;

  const canDisconnect = instance.authMode !== 'none' && !usesSharedCredential(instance) && instance.isAuthenticated;
  const menuEntries: McpMenuEntry[] = [];
  if (instance.url) {
    const serverUrl = instance.url;
    menuEntries.push({
      kind: 'item',
      id: 'copy-url',
      icon: 'content_copy',
      label: t('workspace.mcpServers.configPanel.menu.copyUrl'),
      onSelect: () =>
        void copy(serverUrl).then((ok) =>
          ok
            ? toast.success(t('workspace.mcpServers.configPanel.menu.copied'))
            : toast.error(t('workspace.mcpServers.configPanel.menu.copyFailed'))
        ),
    });
  }
  if (creatorName) {
    menuEntries.push({
      kind: 'info',
      id: 'added-by',
      icon: 'person',
      label: t('workspace.mcpServers.configPanel.menu.addedBy', { name: creatorName }),
    });
  }
  if (canDisconnect || onDelete) menuEntries.push({ kind: 'separator', id: 'sep' });
  if (canDisconnect) {
    menuEntries.push({
      kind: 'item',
      id: 'disconnect',
      icon: 'link_off',
      label: t('workspace.mcpServers.cta.disconnect'),
      description: t('workspace.mcpServers.configPanel.menu.disconnectHint'),
      danger: true,
      onSelect: () => setConfirmDisconnect(true),
    });
  }
  if (onDelete) {
    menuEntries.push({
      kind: 'item',
      id: 'remove',
      icon: 'delete',
      label: t('workspace.mcpServers.configPanel.menu.remove'),
      description: t(
        instance.scope === 'personal'
          ? 'workspace.mcpServers.configPanel.menu.removeHintPersonal'
          : 'workspace.mcpServers.configPanel.menu.removeHint'
      ),
      danger: true,
      onSelect: onDelete,
    });
  }

  const addedGroup = mcpStatusGroup(connectionState);
  const addedColor = addedGroup === 'ready' ? 'green' : addedGroup === 'action' ? 'blue' : 'amber';

  // Unsaved rule changes are asked about before leaving the page.
  const requestBack = () => {
    if (rulesEditor.changes > 0) setConfirmDiscard(true);
    else onBack();
  };

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        overflowY: 'auto',
        paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : PAGE_PADDING_TOP,
        paddingInline: pagePaddingX(100),
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0, width: '100%' }}>
        <button
          type="button"
          onClick={requestBack}
          className="touch-hit"
          style={{
            appearance: 'none',
            margin: 0,
            padding: 0,
            border: 'none',
            background: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="arrow_back" size={16} color="var(--gray-11)" />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.mcpServers.details.breadcrumbLabel')}
          </Text>
        </button>
        <MaterialIcon name="chevron_right" size={14} color="var(--gray-11)" />
        <Text size="2" weight="medium" truncate style={{ color: 'var(--gray-12)' }}>
          {instance.name}
        </Text>
      </Flex>

      <Flex align="start" gap="3" style={{ width: '100%' }}>
        <Flex
          align="center"
          justify="center"
          style={{
            width: 48,
            height: 48,
            borderRadius: 'var(--radius-3)',
            backgroundColor: 'var(--gray-a3)',
            flexShrink: 0,
          }}
        >
          <McpServerIcon instanceId={instance._id} typeId={instance.typeId} name={instance.name} size={26} />
        </Flex>
        <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
          <Flex align="center" gap="2" wrap="wrap">
            <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)', overflowWrap: 'anywhere' }}>
              {instance.name}
            </Heading>
            <span data-testid="mcp-server-status" style={{ display: 'inline-flex' }}>
              {isMcpInstanceDisabled(instance) ? <McpDisabledBadge instance={instance} /> : <McpStatusBadge state={connectionState} />}
            </span>
            <McpInstanceBadges instance={instance} />
          </Flex>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {details.join(' · ')}
          </Text>
          {instance.description ? (
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {instance.description}
            </Text>
          ) : null}
          {replacedNotice ? (
            <Text size="2" style={{ color: 'var(--amber-11)' }}>
              {replacedNotice}
            </Text>
          ) : null}
        </Flex>
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          {onEdit && (
            <Button
              size={isMobile ? '3' : '2'}
              variant="soft"
              color="gray"
              onClick={onEdit}
              aria-label={t('workspace.mcpServers.cta.edit')}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="edit" size={16} color="var(--gray-11)" />
              {!isMobile && t('workspace.mcpServers.cta.edit')}
            </Button>
          )}
          <McpServerActionsMenu entries={menuEntries} container={null} />
        </Flex>
      </Flex>

      <McpInheritedCallout instance={instance} />

      {justAdded && (
        <Flex
          role="status"
          align="center"
          gap="3"
          wrap="wrap"
          data-testid="mcp-server-added"
          style={{
            width: '100%',
            padding: 'var(--space-3)',
            borderRadius: 'var(--radius-3)',
            backgroundColor: `var(--${addedColor}-a3)`,
          }}
        >
          <MaterialIcon name="check_circle" size={18} color={`var(--${addedColor}-11)`} />
          <Text size="2" style={{ flex: '1 1 240px', minWidth: 0, color: `var(--${addedColor}-11)` }}>
            {t(
              addedGroup === 'ready'
                ? 'workspace.mcpServers.added.ready'
                : addedGroup === 'action'
                  ? 'workspace.mcpServers.added.signIn'
                  : 'workspace.mcpServers.added.other',
              { name: instance.name }
            )}
          </Text>
          {onDismissAdded && (
            <Button size="1" variant="soft" color="gray" onClick={onDismissAdded} style={{ cursor: 'pointer' }}>
              {t('workspace.mcpServers.added.dismiss')}
            </Button>
          )}
        </Flex>
      )}

      <Box style={{ width: '100%' }}>
        <McpToolsApprovalsTab
          instance={instance}
          state={connectionState}
          load={toolsLoad}
          editor={rulesEditable ? rulesEditor : null}
          isBusy={isBusy}
          onRefresh={() => void loadTools(false)}
          onAuthenticate={onAuthenticate}
          onReauthenticate={onReauthenticate}
          onEditServer={onEdit}
        />
      </Box>

      {adminSection !== null && (
        <Flex ref={adminSectionRef} direction="column" gap="3" style={{ width: '100%' }} data-testid="mcp-server-admin-section">
          <Heading as="h2" size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {adminSection.label}
          </Heading>
          {adminSection.content}
        </Flex>
      )}

      <McpDisconnectDialog
        instance={confirmDisconnect ? instance : null}
        onOpenChange={(open) => {
          if (!open && !isBusy) setConfirmDisconnect(false);
        }}
        isLoading={confirmDisconnect && isBusy}
        onConfirm={() => {
          void Promise.resolve(onDisconnect()).finally(() => setConfirmDisconnect(false));
        }}
      />

      <ConfirmationDialog
        open={confirmDiscard}
        onOpenChange={setConfirmDiscard}
        title={t('workspace.mcpServers.configPanel.discardTitle')}
        message={t('workspace.mcpServers.configPanel.discardBody', { count: rulesEditor.changes })}
        confirmLabel={t('workspace.mcpServers.configPanel.discard')}
        confirmVariant="danger"
        onConfirm={() => {
          rulesEditor.discard();
          setConfirmDiscard(false);
          onBack();
        }}
      />
    </Flex>
  );
}
