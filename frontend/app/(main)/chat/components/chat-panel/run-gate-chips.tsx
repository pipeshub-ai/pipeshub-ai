'use client';

import { Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useChatStore } from '@/chat/store';
import { agentGateLocks, useRunGatesStore, type TurnGateKey } from '@/chat/run-gates';

const CHIPS: ReadonlyArray<{ key: TurnGateKey; icon: string; label: string }> = [
  { key: 'dryRun', icon: 'science', label: 'chat.runGates.dryRun' },
  { key: 'planApproval', icon: 'checklist', label: 'chat.runGates.planApproval' },
  { key: 'verify', icon: 'fact_check', label: 'chat.runGates.verify' },
];

/** The gates this message will run under, for the composer to leave out an empty chip row. */
export function useRunGateChipsShown(enabled: boolean, agentScoped: boolean): boolean {
  const turn = useRunGatesStore((s) => s.turn);
  const agent = useChatStore((s) => (agentScoped ? s.agentGates : null));
  if (!enabled) return false;
  const locks = agentGateLocks(agent);
  return turn.dryRun || turn.planApproval || turn.verify || locks.planApproval || locks.verify;
}

/**
 * Over the composer, like the repository chip: one chip per gate the next message runs under (WP-31 A). × turns a
 * turn's own gate off; an agent's own gate has no ×, since every run of it is held to it.
 */
export function RunGateChips({ agentScoped }: { agentScoped: boolean }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const turn = useRunGatesStore((s) => s.turn);
  const setGate = useRunGatesStore((s) => s.set);
  const agent = useChatStore((s) => (agentScoped ? s.agentGates : null));
  const locks = agentGateLocks(agent);
  return (
    <>
      {CHIPS.map(({ key, icon, label }) => {
        const locked = key !== 'dryRun' && locks[key];
        if (!locked && !turn[key]) return null;
        const name = t(label);
        return (
          <Flex
            key={key}
            align="center"
            gap="1"
            data-testid={`run-gate-chip-${key}`}
            data-locked={locked ? 'true' : undefined}
            style={{
              alignSelf: 'flex-start',
              padding: locked ? '0 var(--space-2)' : '0 var(--space-1) 0 var(--space-2)',
              minHeight: isMobile ? 44 : 28,
              borderRadius: 'var(--radius-2)',
              border: '1px solid var(--accent-7)',
              background: 'var(--accent-3)',
            }}
          >
            <MaterialIcon name={icon} size={14} color="var(--accent-11)" />
            <Text size="1" style={{ color: 'var(--accent-11)' }}>
              {locked ? t('chat.runGates.chipAgent', { gate: name }) : name}
            </Text>
            {locked ? null : (
              <IconButton
                size="1"
                variant="ghost"
                color="gray"
                aria-label={t('chat.runGates.turnOff', { gate: name })}
                onClick={() => setGate(key, false)}
                style={isMobile ? { minWidth: 44, minHeight: 44 } : undefined}
              >
                <MaterialIcon name="close" size={14} color="var(--slate-11)" />
              </IconButton>
            )}
          </Flex>
        );
      })}
    </>
  );
}
