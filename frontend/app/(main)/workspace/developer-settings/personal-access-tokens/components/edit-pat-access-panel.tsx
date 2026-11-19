'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  KnowledgeLimitChoice,
  isKnowledgeLimitValid,
  knowledgeLimitForApi,
  limitSize,
  sameKnowledgeLimit,
  selectionFromLimit,
  type KnowledgeLimit,
  type KnowledgeLimitValue,
} from '@/app/components/knowledge-limit';
import { useToastStore } from '@/lib/store/toast-store';
import { WorkspaceRightPanel } from '../../../components/workspace-right-panel';
import { PatApi } from '../api';
import type { PatListItem } from '../types';

const CARD_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-2)',
  padding: 'var(--space-4)',
};

export interface EditPatAccessPanelProps {
  /** The token being changed; the panel is open while one is set. */
  token: PatListItem | null;
  onOpenChange: (open: boolean) => void;
  /** Called with the token as the server now has it, after a save or an undo. */
  onSaved: (token: PatListItem) => void;
}

/**
 * Changes what an existing personal access token can see. The token and its
 * secret stay the same, so nothing that uses it needs updating.
 */
export function EditPatAccessPanel({ token, onOpenChange, onSaved }: EditPatAccessPanelProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [value, setValue] = useState<KnowledgeLimitValue>(null);
  const [isSaving, setIsSaving] = useState(false);

  // Re-seed whenever another token is opened.
  const tokenId = token?.id;
  // A save that finishes after the user moved on must not close what they opened next.
  const openTokenId = useRef(tokenId);
  openTokenId.current = tokenId;
  useEffect(() => {
    setValue(selectionFromLimit(token?.knowledgeLimit));
  }, [tokenId]);

  const next = knowledgeLimitForApi(value);
  const changed = token !== null && !sameKnowledgeLimit(next, token.knowledgeLimit);
  const valid = isKnowledgeLimitValid(value);

  const describe = useCallback(
    (name: string, limit: KnowledgeLimit | undefined) =>
      limit
        ? t('workspace.personalAccessTokens.access.savedSome', { name, count: limitSize(limit) })
        : t('workspace.personalAccessTokens.access.savedEverything', { name }),
    [t]
  );

  const undo = useCallback(
    async (target: PatListItem) => {
      try {
        const { token: restored } = await PatApi.updateKnowledgeLimit(target.id, target.knowledgeLimit ?? null);
        onSaved(restored);
        addToast({
          variant: 'success',
          title: t('workspace.personalAccessTokens.access.undoneTitle'),
          description: describe(restored.name, restored.knowledgeLimit),
          duration: 3000,
        });
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.personalAccessTokens.access.undoErrorTitle'),
          duration: 5000,
        });
      }
    },
    [addToast, describe, onSaved, t]
  );

  const handleSave = useCallback(async () => {
    if (!token || !changed || !valid) return;
    const before = token;
    setIsSaving(true);
    try {
      const { token: updated } = await PatApi.updateKnowledgeLimit(token.id, next ?? null);
      onSaved(updated);
      if (openTokenId.current === before.id) onOpenChange(false);
      addToast({
        variant: 'success',
        title: t('workspace.personalAccessTokens.access.savedTitle'),
        description: describe(updated.name, updated.knowledgeLimit),
        duration: 8000,
        action: {
          label: t('workspace.personalAccessTokens.access.undo'),
          onClick: () => void undo(before),
        },
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.personalAccessTokens.access.errorTitle'),
        description: t('workspace.personalAccessTokens.access.errorDescription'),
        duration: 5000,
      });
    } finally {
      setIsSaving(false);
    }
  }, [token, changed, valid, next, onSaved, onOpenChange, addToast, t, describe, undo]);

  return (
    <WorkspaceRightPanel
      open={token !== null}
      onOpenChange={onOpenChange}
      title={t('workspace.personalAccessTokens.access.panelTitle')}
      icon={<MaterialIcon name="tune" size={20} color="var(--slate-12)" />}
      primaryLabel={t('workspace.personalAccessTokens.access.save')}
      secondaryLabel={t('workspace.personalAccessTokens.create.cancel')}
      primaryDisabled={!changed || !valid}
      primaryLoading={isSaving}
      onPrimaryClick={() => void handleSave()}
      onSecondaryClick={() => onOpenChange(false)}
    >
      {token && (
        <Flex direction="column" gap="4">
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.personalAccessTokens.access.intro', { name: token.name })}
          </Text>
          <Box style={CARD_STYLE}>
            <KnowledgeLimitChoice
              appName={token.name}
              value={value}
              onChange={setValue}
              idPrefix="pat-access"
            />
          </Box>
        </Flex>
      )}
    </WorkspaceRightPanel>
  );
}
