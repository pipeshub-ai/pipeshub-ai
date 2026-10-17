import type { AgentSkillReference } from '../types';

/**
 * Warning copy for a reconstructed skill node. Uses `status` from the
 * agent projection only — never the current user's assignable catalog,
 * which would false-flag skills another user attached to a shared agent.
 */
export function skillNodeWarning(
  skill: AgentSkillReference,
  t: (key: string, options?: Record<string, unknown>) => string,
): string | undefined {
  const status = (skill.status ?? '').toLowerCase();
  if (status === 'disabled') {
    return t('agentBuilder.skillDisabled');
  }
  if (status !== 'deprecated') {
    return undefined;
  }
  if (skill.replacedBy) {
    return t('agentBuilder.skillDeprecatedReplacedBy', { name: skill.replacedBy });
  }
  return t('agentBuilder.skillDeprecated');
}
