'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { instructionsFromAnswer, useInstructionsDraft } from '@/app/(main)/agents/agent-builder/instructions-draft';
import { useChatStore } from '../../store';

/**
 * Under an `/init` answer (SLASH2 C4): opens the drafted instructions in the agent editor, for the agent's
 * editors only (O4). It saves nothing; the editor does, when the person saves.
 */
export function InitDraftAction({ answer }: { answer: string }) {
  const { t } = useTranslation();
  const router = useRouter();
  const canEdit = useChatStore((s) => Boolean(s.agentContextAccess?.canEdit));
  const agentKey = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.threadAgentId : null));
  if (!canEdit || !agentKey) return null;
  const open = () => {
    useInstructionsDraft.getState().offer(agentKey, instructionsFromAnswer(answer));
    router.push(`/agents/edit?agentKey=${encodeURIComponent(agentKey)}`);
  };
  return (
    <Flex align="center" gap="2" wrap="wrap" style={{ margin: 'var(--space-2) 0' }} data-testid="init-draft-action">
      <Button size="1" variant="soft" onClick={open} style={{ minHeight: '28px' }}>
        <MaterialIcon name="edit" size={14} />
        {t('slashCommands.result.init.open')}
      </Button>
      <Text size="1" color="gray">
        {t('slashCommands.result.init.notSaved')}
      </Text>
    </Flex>
  );
}
