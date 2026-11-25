import type { TFunction } from 'i18next';
import { engineName } from '@/app/(main)/workspace/ai-accounts/access-copy';
import type { CodeTaskHandoff } from './types';

/** The reasons the chat words (`x.pipeshub.run.handoff`); any other falls back to "many steps". */
export const HANDOFF_REASONS = ['research', 'analysis', 'planning', 'long_report', 'many_steps', 'context', 'budget'] as const;

/** A hand-off as the stream or a saved part carries it; null when it is not one. */
export function toCodeTaskHandoff(raw: unknown): CodeTaskHandoff | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const { trigger, reason, engine } = raw as Record<string, unknown>;
  if (typeof trigger !== 'string' || typeof reason !== 'string') return null;
  return { trigger, reason, ...(typeof engine === 'string' && engine ? { engine } : {}) };
}

/** "Handed to Claude Code: this needs research across many sources" (DEEPROUTE). */
export function handoffLabel(t: TFunction, handoff: CodeTaskHandoff): string {
  const reason = (HANDOFF_REASONS as readonly string[]).includes(handoff.reason) ? handoff.reason : 'many_steps';
  return t('agentActivity.handoff.label', {
    engine: handoff.engine ? engineName(handoff.engine) : t('agentActivity.handoff.engineFallback'),
    reason: t(`agentActivity.handoff.reason.${reason}`),
  });
}
