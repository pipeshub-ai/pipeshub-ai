'use client';

import React, { useState } from 'react';
import { Box, Flex, Text, TextArea } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import type { AppliedFilters } from '@/chat/types';
import type { ProjectDetail, ProjectKnowledgeScope } from '@/chat/project-types';
import { ConnectorsCard } from './connectors-card';
import { FilesCard } from './files-card';
import { ToolsMcpCard } from './tools-mcp-card';

interface CollapsibleCardProps {
  icon: string;
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  defaultExpanded?: boolean;
}

/**
 * Right-panel card shell — collapsible section with a header row
 * (icon + title + optional action) and expandable body.
 */
function CollapsibleCard({ icon, title, action, children, defaultExpanded = false }: CollapsibleCardProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  return (
    <Box
      style={{
        background: 'var(--olive-2)',
        border: '1px solid var(--olive-4)',
        borderRadius: 'var(--radius-3)',
        overflow: 'hidden',
      }}
    >
      <Flex
        align="center"
        justify="between"
        gap="2"
        style={{ padding: 'var(--space-3)', cursor: 'pointer' }}
        onClick={() => setIsExpanded((v) => !v)}
      >
        <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
          <MaterialIcon name={icon} size={16} color="var(--slate-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {title}
          </Text>
        </Flex>
        <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
          {isExpanded && action && <span onClick={(e) => e.stopPropagation()}>{action}</span>}
          <MaterialIcon
            name="expand_more"
            size={18}
            color="var(--slate-10)"
            style={{
              transform: isExpanded ? 'rotate(0deg)' : 'rotate(-90deg)',
              transition: 'transform 0.15s ease',
              display: 'block',
            }}
          />
        </Flex>
      </Flex>
      {isExpanded && <Box style={{ padding: '0 var(--space-3) var(--space-3)' }}>{children}</Box>}
    </Box>
  );
}

export interface ProjectSettingsPanelProps {
  project: ProjectDetail;
  canEdit: boolean;
  isOwner: boolean;

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
 * Right-side settings panel for the redesigned project workspace —
 * collapsible cards (Instructions, Files, Connectors, Tools & MCP, Members). State and
 * mutation handlers are owned by the parent workspace component; this is
 * presentation only, except for the Files card, which owns its own KB
 * fetch/upload/delete lifecycle (see `FilesCard`).
 */
export function ProjectSettingsPanel({
  project,
  canEdit,
  isOwner,
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

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      {/* Instructions */}
      <CollapsibleCard
        icon="description"
        title={t('chat.projects.workspace.instructionsTitle')}
        action={
          canEdit && !isEditingInstructions ? (
            <LoadingButton size="1" variant="ghost" color="gray" onClick={onStartEditInstructions}>
              {t('chat.projects.workspace.editInstructions')}
            </LoadingButton>
          ) : undefined
        }
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
      </CollapsibleCard>

      {/* Files (backed by the project's hidden linked Collection) */}
      <CollapsibleCard icon="attach_file" title={t('chat.projects.workspace.filesTitle')}>
        <FilesCard
          projectId={project._id}
          linkedKnowledgeBaseId={project.linkedKnowledgeBaseId}
          canEdit={canEdit}
          onKbCreated={onKbCreated}
        />
      </CollapsibleCard>

      {/* Connectors (knowledgeScope.apps — indexed sources, not action toolsets) */}
      <CollapsibleCard
        icon="hub"
        title={t('chat.projects.workspace.connectorsTitle', { defaultValue: 'Connectors' })}
      >
        <ConnectorsCard
          selectedAppIds={project.knowledgeScope?.apps ?? []}
          knowledgeScopeKb={project.knowledgeScope?.kb ?? []}
          appliedFiltersKb={project.appliedFilters?.kb ?? []}
          previousAppliedApps={project.appliedFilters?.apps ?? []}
          canEdit={canEdit}
          onChange={onConnectorsChange}
        />
      </CollapsibleCard>

      {/* Tools & MCP */}
      <CollapsibleCard icon="build" title={t('chat.projects.workspace.toolsTitle', { defaultValue: 'Tools & MCP' })}>
        <ToolsMcpCard selectedTools={project.tools ?? []} canEdit={canEdit} onChange={onToolsChange} />
      </CollapsibleCard>

      {/* Members */}
      <CollapsibleCard
        icon="group"
        title={t('chat.projects.workspace.membersTitle')}
        action={
          isOwner ? (
            <LoadingButton size="1" variant="ghost" color="gray" onClick={onOpenShare}>
              {t('chat.projects.workspace.share')}
            </LoadingButton>
          ) : undefined
        }
      >
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t(
            project.members.length + 1 === 1
              ? 'chat.projects.workspace.memberCount_one'
              : 'chat.projects.workspace.memberCount_other',
            { count: project.members.length + 1 },
          )}
        </Text>
      </CollapsibleCard>
    </Flex>
  );
}
