'use client';

import React, { useMemo, useState } from 'react';
import { Box, Flex, IconButton, Text, TextArea, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { formatFileSize } from '@/app/components/file-preview/utils';
import type { AppliedFilters } from '@/chat/types';
import type { ProjectDetail, ProjectKnowledgeScope } from '@/chat/project-types';
import { ConnectorsCard } from './connectors-card';
import { FilesCard } from './files-card';
import { ToolsMcpCard } from './tools-mcp-card';
import { PanelCard, PanelHeader } from './panel-section';

export type SetupSectionKey = 'instructions' | 'files' | 'connectors' | 'tools' | 'members';

const SETUP_SECTION_ORDER: SetupSectionKey[] = ['instructions', 'files', 'connectors', 'tools', 'members'];

interface SetupRowProps {
  icon: string;
  label: string;
  /** Right-aligned summary shown when collapsed, replaced by `action` (if any) while expanded. A plain string renders as muted ellipsized text; pass a node for richer values. */
  statusText?: React.ReactNode;
  action?: React.ReactNode;
  isFirst: boolean;
  isExpanded: boolean;
  onToggle: () => void;
  children?: React.ReactNode;
  /** When false, the row never expands — `onToggle` is the click action (e.g. open a drawer). */
  expandable?: boolean;
  clickable?: boolean;
  /** Mount the body only once the row is first expanded. For bodies that fetch on mount and report nothing while collapsed. */
  lazy?: boolean;
}

/**
 * One row in the "Project setup" list. Once mounted, the expandable body
 * stays mounted (just visually hidden) so `FilesCard`'s file count keeps
 * reporting to the parent even while its row is collapsed — see
 * `onSummaryChange` below.
 */
function SetupRow({
  icon,
  label,
  statusText,
  action,
  isFirst,
  isExpanded,
  onToggle,
  children,
  expandable = true,
  clickable = true,
  lazy = false,
}: SetupRowProps) {
  const expanded = expandable && isExpanded;
  // Set during render, not in an effect, so the first expansion never paints an empty body.
  const [hasExpanded, setHasExpanded] = useState(expanded);
  if (expanded && !hasExpanded) {
    setHasExpanded(true);
  }
  return (
    <Box style={{ borderTop: isFirst ? 'none' : '1px solid var(--olive-4)' }}>
      <Flex
        align="center"
        justify="between"
        gap="2"
        role={clickable ? 'button' : undefined}
        tabIndex={clickable ? 0 : undefined}
        onClick={clickable ? onToggle : undefined}
        onKeyDown={
          clickable
            ? (e) => {
                // A key pressed on the nested `action` button bubbles here; handling it
                // would cancel that button's own activation and toggle the row instead.
                if (e.target !== e.currentTarget) return;
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onToggle();
                }
              }
            : undefined
        }
        style={{ padding: 'var(--space-3) 0', cursor: clickable ? 'pointer' : 'default' }}
      >
        <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
          <MaterialIcon name={icon} size={16} color="var(--slate-10)" />
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {label}
          </Text>
        </Flex>
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          {expandable && expanded && action ? (
            <span onClick={(e) => e.stopPropagation()}>{action}</span>
          ) : typeof statusText === 'string' ? (
            <Text
              size="1"
              style={{
                color: 'var(--slate-9)',
                maxWidth: 130,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {statusText}
            </Text>
          ) : (
            statusText
          )}
          {clickable && (
            <MaterialIcon
              name="chevron_right"
              size={16}
              color="var(--slate-8)"
              style={{
                transform: expanded ? 'rotate(90deg)' : 'none',
                transition: expandable ? 'transform 0.15s ease' : undefined,
                display: 'block',
                flexShrink: 0,
              }}
            />
          )}
        </Flex>
      </Flex>
      {expandable && (!lazy || hasExpanded) && (
        <Box style={{ display: expanded ? 'block' : 'none', paddingBottom: 'var(--space-3)' }}>{children}</Box>
      )}
    </Box>
  );
}

/** Collapsed-row status for a binary "is this configured yet" field — a checkmark once set, plain muted text otherwise. */
function SetStatus({ done, text }: { done: boolean; text: string }) {
  return (
    <Flex align="center" gap="1">
      {done && <MaterialIcon name="check" size={14} color="var(--jade-9)" />}
      <Text size="1" style={{ color: done ? 'var(--jade-11)' : 'var(--slate-9)' }}>
        {text}
      </Text>
    </Flex>
  );
}

export interface ProjectSettingsPanelProps {
  project: ProjectDetail;
  canEdit: boolean;
  isOwner: boolean;

  expandedSection: SetupSectionKey | null;
  onExpandedSectionChange: (section: SetupSectionKey | null) => void;

  instructionsDraft: string;
  isEditingInstructions: boolean;
  isSavingInstructions: boolean;
  onInstructionsDraftChange: (value: string) => void;
  onStartEditInstructions: () => void;
  onCancelEditInstructions: () => void;
  onSaveInstructions: () => void;

  onKbCreated: (kbId: string) => void;
  onConnectorsChange: (patch: {
    knowledgeScope: ProjectKnowledgeScope;
    appliedFilters: AppliedFilters;
  }) => void;
  onToolsChange: (tools: string[]) => void;

  onOpenShare: () => void;
}

/**
 * Right-panel "Project setup" card for the redesigned project workspace —
 * a single-open accordion of Instructions/Files/Connectors/Tools & MCP, plus a
 * Members row that opens the share drawer instead of expanding. Each collapsed
 * row is an icon + status summary + chevron. State and mutation handlers are
 * owned by the parent workspace component; this is presentation only, except
 * for the Files card, which owns its own KB fetch/upload/delete lifecycle
 * (see `FilesCard`).
 */
export function ProjectSettingsPanel({
  project,
  canEdit,
  isOwner,
  expandedSection,
  onExpandedSectionChange,
  instructionsDraft,
  isEditingInstructions,
  isSavingInstructions,
  onInstructionsDraftChange,
  onStartEditInstructions,
  onCancelEditInstructions,
  onSaveInstructions,
  onKbCreated,
  onConnectorsChange,
  onToolsChange,
  onOpenShare,
}: ProjectSettingsPanelProps) {
  const { t } = useTranslation();

  // `FilesCard` stays mounted regardless of `expandedSection` (see `SetupRow`)
  // so this keeps reporting a live count/size for the collapsed-row status text.
  const [fileSummary, setFileSummary] = useState({ count: 0, totalBytes: 0 });

  const hasInstructions = Boolean(project.instructions?.trim());
  const connectorCount = project.knowledgeScope?.apps?.length ?? 0;
  const toolCount = project.tools?.length ?? 0;
  const memberCount = project.members.length; // additional members beyond the owner

  const setupProgress = useMemo(
    () =>
      [hasInstructions, fileSummary.count > 0, connectorCount > 0, toolCount > 0, memberCount > 0].filter(Boolean)
        .length,
    [hasInstructions, fileSummary.count, connectorCount, toolCount, memberCount],
  );

  const toggleSection = (key: SetupSectionKey) => {
    const opening = expandedSection !== key;
    if (key === 'instructions' && !opening && isEditingInstructions) {
      onCancelEditInstructions();
    }
    onExpandedSectionChange(opening ? key : null);
  };

  return (
    <PanelCard>
      <PanelHeader
        title={t('chat.projects.workspace.projectSetupTitle', { defaultValue: 'Project setup' })}
        trailing={
          <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
            {t('chat.projects.workspace.projectSetupProgress', {
              defaultValue: '{{done}} / {{total}}',
              done: setupProgress,
              total: SETUP_SECTION_ORDER.length,
            })}
          </Text>
        }
      />
      <Box style={{ padding: '0 var(--space-4)' }}>
        {/* Instructions */}
        <SetupRow
          icon="description"
          label={t('chat.projects.workspace.instructionsTitle')}
          statusText={
            <SetStatus
              done={hasInstructions}
              text={
                hasInstructions
                  ? t('chat.projects.workspace.statusSet', { defaultValue: 'Set' })
                  : t('chat.projects.workspace.statusNotSet', { defaultValue: 'Not set' })
              }
            />
          }
          action={
            canEdit && !isEditingInstructions ? (
              <Tooltip content={t('chat.projects.workspace.editInstructions')}>
                <IconButton
                  size="1"
                  variant="soft"
                  radius="large"
                  aria-label={t('chat.projects.workspace.editInstructions')}
                  onClick={onStartEditInstructions}
                >
                  <MaterialIcon name="edit" size={14} color="var(--accent-11)" />
                </IconButton>
              </Tooltip>
            ) : undefined
          }
          isFirst
          isExpanded={expandedSection === 'instructions'}
          onToggle={() => toggleSection('instructions')}
        >
          {isEditingInstructions ? (
            <Flex direction="column" gap="2">
              <TextArea
                value={instructionsDraft}
                onChange={(e) => onInstructionsDraftChange(e.target.value)}
                placeholder={t('chat.projects.workspace.instructionsPlaceholder')}
                rows={5}
                maxLength={8000}
                autoFocus
              />
              <Flex gap="2" justify="end">
                <LoadingButton
                  size="1"
                  variant="soft"
                  color="gray"
                  onClick={onCancelEditInstructions}
                  disabled={isSavingInstructions}
                >
                  {t('action.cancel')}
                </LoadingButton>
                <LoadingButton
                  size="1"
                  color="jade"
                  onClick={onSaveInstructions}
                  loading={isSavingInstructions}
                  loadingLabel={t('chat.projects.workspace.saving')}
                >
                  {t('chat.projects.workspace.save')}
                </LoadingButton>
              </Flex>
            </Flex>
          ) : (
            <Text
              size="2"
              style={{
                color: project.instructions ? 'var(--slate-12)' : 'var(--slate-10)',
                whiteSpace: 'pre-wrap',
              }}
            >
              {project.instructions?.trim() || t('chat.projects.workspace.noInstructions')}
            </Text>
          )}
        </SetupRow>

        {/* Files (backed by the project's hidden linked Collection) */}
        <SetupRow
          icon="attach_file"
          label={t('chat.projects.workspace.filesTitle')}
          statusText={
            fileSummary.count === 0
              ? t('chat.projects.workspace.filesStatusNone', { defaultValue: 'No files' })
              : fileSummary.totalBytes > 0
                ? t('chat.projects.workspace.filesStatusCountWithSize', {
                    count: fileSummary.count,
                    size: formatFileSize(fileSummary.totalBytes),
                    defaultValue: '{{count}} files · {{size}}',
                  })
                : t('chat.projects.workspace.filesStatusCount', {
                    count: fileSummary.count,
                    defaultValue: '{{count}} files',
                  })
          }
          isFirst={false}
          isExpanded={expandedSection === 'files'}
          onToggle={() => toggleSection('files')}
        >
          <FilesCard
            projectId={project._id}
            linkedKnowledgeBaseId={project.linkedKnowledgeBaseId}
            canEdit={canEdit}
            onKbCreated={onKbCreated}
            onSummaryChange={setFileSummary}
          />
        </SetupRow>

        {/* Connectors (knowledgeScope.apps — indexed sources, not action toolsets) */}
        <SetupRow
          icon="hub"
          label={t('chat.projects.workspace.connectorsTitle', { defaultValue: 'Connectors' })}
          statusText={
            connectorCount > 0
              ? t('chat.projects.workspace.connectorsStatusCount', {
                  count: connectorCount,
                  defaultValue: '{{count}} connected',
                })
              : t('chat.projects.workspace.connectorsStatusNone', { defaultValue: 'None' })
          }
          isFirst={false}
          isExpanded={expandedSection === 'connectors'}
          onToggle={() => toggleSection('connectors')}
          lazy
        >
          <ConnectorsCard
            selectedAppIds={project.knowledgeScope?.apps ?? []}
            knowledgeScopeKb={project.knowledgeScope?.kb ?? []}
            appliedFiltersKb={project.appliedFilters?.kb ?? []}
            previousAppliedApps={project.appliedFilters?.apps ?? []}
            canEdit={canEdit}
            onChange={onConnectorsChange}
          />
        </SetupRow>

        {/* Tools & MCP */}
        <SetupRow
          icon="build"
          label={t('chat.projects.workspace.toolsTitle', { defaultValue: 'Tools & MCP' })}
          statusText={
            toolCount > 0
              ? t('chat.projects.workspace.toolsStatusCount', { count: toolCount, defaultValue: '{{count}} enabled' })
              : t('chat.projects.workspace.toolsStatusDefault', { defaultValue: 'Default' })
          }
          isFirst={false}
          isExpanded={expandedSection === 'tools'}
          onToggle={() => toggleSection('tools')}
          lazy
        >
          <ToolsMcpCard selectedTools={project.tools ?? []} canEdit={canEdit} onChange={onToolsChange} />
        </SetupRow>

        {/* Members */}
        <SetupRow
          icon="group"
          label={t('chat.projects.workspace.membersTitle')}
          isFirst={false}
          isExpanded={false}
          expandable={false}
          clickable={isOwner}
          onToggle={onOpenShare}
        />
      </Box>
    </PanelCard>
  );
}
