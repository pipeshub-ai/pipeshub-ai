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
import { isProcessedError } from '@/lib/api/api-error';
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
  /** Re-reads the tokens from the server, for when a change's outcome is unknown. */
  onReload: () => Promise<void>;
}

/** The server answered and refused, so the stored limit is unchanged. */
const isRefusal = (error: unknown): boolean =>
  isProcessedError(error) &&
  error.statusCode !== undefined &&
  error.statusCode >= 400 &&
  error.statusCode < 500;

/**
 * Changes what an existing personal access token can see. The token and its
 * secret stay the same, so nothing that uses it needs updating.
 */
export function EditPatAccessPanel({ token, onOpenChange, onSaved, onReload }: EditPatAccessPanelProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [value, setValue] = useState<KnowledgeLimitValue>(null);
  const [isSaving, setIsSaving] = useState(false);

  const tokenId = token?.id;
  // Each opening is its own session: a save that finishes after the panel was
  // closed or moved to another token must not close what is open now, even
  // when it is the same token opened again.
  const session = useRef(0);
  useEffect(() => {
    session.current += 1;
  }, [tokenId]);
  // Seed on every opening. When the server then reports a new limit for the
  // open token (an Undo, a reload, an earlier save landing), follow it only
  // while the user has not changed the choice in this session. Seeded during
  // render, not in an effect, so the choice below never paints with another
  // token's selection.
  const storedKey = JSON.stringify(token?.knowledgeLimit ?? null);
  const [seeded, setSeeded] = useState<{ id?: string; key: string; limit?: KnowledgeLimit }>({ key: 'null' });
  if (seeded.id !== tokenId || seeded.key !== storedKey) {
    const stored = token?.knowledgeLimit;
    setSeeded({ id: tokenId, key: storedKey, limit: stored });
    if (seeded.id !== tokenId || sameKnowledgeLimit(knowledgeLimitForApi(value), seeded.limit)) {
      setValue(selectionFromLimit(stored));
    }
  }

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

  // No answer (network, timeout, 5xx) means the change may have landed. Show
  // the token as the server has it instead of claiming nothing changed.
  const reportUnknown = useCallback(async () => {
    const reloaded = await onReload().then(
      () => true,
      () => false
    );
    addToast({
      variant: 'error',
      title: t('workspace.personalAccessTokens.access.unknownTitle'),
      // If the re-read failed too, what the panel shows may be stale.
      description: reloaded
        ? t('workspace.personalAccessTokens.access.unknownDescription')
        : t('workspace.personalAccessTokens.access.unknownReloadFailedDescription'),
      duration: 8000,
    });
  }, [addToast, onReload, t]);

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
      } catch (error) {
        if (isRefusal(error)) {
          addToast({
            variant: 'error',
            title: t('workspace.personalAccessTokens.access.undoErrorTitle'),
            duration: 5000,
          });
        } else {
          await reportUnknown();
        }
      }
    },
    [addToast, describe, onSaved, reportUnknown, t]
  );

  const handleSave = useCallback(async () => {
    if (!token || !changed || !valid) return;
    const before = token;
    const mine = session.current;
    setIsSaving(true);
    try {
      const { token: updated } = await PatApi.updateKnowledgeLimit(token.id, next ?? null);
      onSaved(updated);
      if (session.current === mine) onOpenChange(false);
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
    } catch (error) {
      if (isRefusal(error)) {
        addToast({
          variant: 'error',
          title: t('workspace.personalAccessTokens.access.errorTitle'),
          description: t('workspace.personalAccessTokens.access.errorDescription'),
          duration: 5000,
        });
      } else {
        await reportUnknown();
      }
    } finally {
      setIsSaving(false);
    }
  }, [token, changed, valid, next, onSaved, onOpenChange, addToast, t, describe, undo, reportUnknown]);

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
            {/* A new token starts with no remembered pick: "only" must never offer another token's items. */}
            <KnowledgeLimitChoice
              key={token.id}
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
