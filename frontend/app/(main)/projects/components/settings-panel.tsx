'use client';

import React, { useRef, useState } from 'react';
import { Box, Flex, Text, TextArea } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { formatFileSize } from '@/app/components/file-preview/utils';
import type { ProjectDetail } from '@/chat/project-types';

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
function CollapsibleCard({ icon, title, action, children, defaultExpanded = true }: CollapsibleCardProps) {
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
          {isExpanded && action && (
            <span onClick={(e) => e.stopPropagation()}>{action}</span>
          )}
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
      {isExpanded && (
        <Box style={{ padding: '0 var(--space-3) var(--space-3)' }}>{children}</Box>
      )}
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

  isUploading: boolean;
  onUploadFiles: (files: FileList | null) => void;
  onRemoveFile: (recordId: string) => void;

  onOpenShare: () => void;
}

/**
 * Right-side settings panel for the redesigned project workspace — three
 * collapsible cards (Instructions, Files, Members). State and mutation
 * handlers are owned by the parent workspace component; this is presentation
 * only.
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
  isUploading,
  onUploadFiles,
  onRemoveFile,
  onOpenShare,
}: ProjectSettingsPanelProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);

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

      {/* Files */}
      <CollapsibleCard
        icon="attach_file"
        title={t('chat.projects.workspace.filesTitle')}
        action={
          canEdit ? (
            <>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                onChange={(e) => onUploadFiles(e.target.files)}
              />
              <LoadingButton
                size="1"
                variant="ghost"
                color="gray"
                onClick={() => fileInputRef.current?.click()}
                loading={isUploading}
                loadingLabel={t('chat.projects.workspace.uploading')}
              >
                {t('chat.projects.workspace.uploadFiles')}
              </LoadingButton>
            </>
          ) : undefined
        }
      >
        {project.files.length === 0 ? (
          <Text size="2" style={{ color: 'var(--slate-10)' }}>
            {t('chat.projects.workspace.noFiles')}
          </Text>
        ) : (
          <Flex direction="column" gap="1">
            {project.files.map((file) => (
              <Flex
                key={file.recordId}
                align="center"
                justify="between"
                style={{
                  padding: 'var(--space-2)',
                  borderRadius: 'var(--radius-2)',
                  background: 'var(--olive-1)',
                }}
              >
                <Flex align="center" gap="2" style={{ minWidth: 0 }}>
                  <MaterialIcon name="description" size={14} color="var(--slate-11)" />
                  <Text
                    size="1"
                    style={{
                      color: 'var(--slate-12)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {file.recordName || file.recordId}
                  </Text>
                  {typeof file.sizeBytes === 'number' && (
                    <Text size="1" style={{ color: 'var(--slate-10)', flexShrink: 0 }}>
                      {formatFileSize(file.sizeBytes)}
                    </Text>
                  )}
                </Flex>
                {canEdit && (
                  <button
                    type="button"
                    aria-label={t('chat.projects.workspace.removeFile')}
                    onClick={() => onRemoveFile(file.recordId)}
                    style={{
                      appearance: 'none',
                      border: 'none',
                      background: 'transparent',
                      cursor: 'pointer',
                      display: 'flex',
                      flexShrink: 0,
                    }}
                  >
                    <MaterialIcon name="close" size={14} color="var(--slate-10)" />
                  </button>
                )}
              </Flex>
            ))}
          </Flex>
        )}
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
