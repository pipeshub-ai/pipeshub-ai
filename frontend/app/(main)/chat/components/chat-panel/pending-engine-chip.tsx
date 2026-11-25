'use client';

import { Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { engineName } from '@/app/(main)/workspace/ai-accounts/access-copy';
import { ENGINE_NONE } from '@/chat/sandbox-commands';
import { useChatStore } from '@/chat/store';

/**
 * The coding engine `/engine` chose before a new chat's first message (CMDUI2), over the composer: "Codex for this
 * chat". Shown only in the new chat of the agent it was chosen for (null: the assistant); × goes back to the
 * person's default.
 */
export function PendingEngineChip({ agentId }: { agentId: string | null }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const held = useChatStore((s) => s.pendingCodingEngine);
  const clear = useChatStore((s) => s.setPendingCodingEngine);
  if (!held || held.agentId !== agentId) return null;
  const name = held.engine === ENGINE_NONE ? t('slashCommands.result.engine.noEngine') : engineName(held.engine);
  return (
    <Flex
      align="center"
      gap="1"
      data-testid="pending-engine-chip"
      style={{
        alignSelf: 'flex-start',
        maxWidth: '100%',
        padding: '0 var(--space-1) 0 var(--space-2)',
        minHeight: isMobile ? 44 : 28,
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-5)',
        background: 'var(--olive-2)',
      }}
    >
      <MaterialIcon name="terminal" size={14} color="var(--slate-11)" />
      <Text size="1" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
        {t('chat.pendingEngine.label', { engine: name })}
      </Text>
      <IconButton
        size="1"
        variant="ghost"
        color="gray"
        aria-label={t('chat.pendingEngine.clear')}
        onClick={() => clear(null)}
        style={isMobile ? { minWidth: 44, minHeight: 44 } : undefined}
      >
        <MaterialIcon name="close" size={14} color="var(--slate-11)" />
      </IconButton>
    </Flex>
  );
}
