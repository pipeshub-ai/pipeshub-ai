import type { SkillUsage } from './types';

// The usage names only the agents and skills the user may see and counts the rest.
// A counted one blocks or detaches a delete exactly as a named one does.

export function isAssignedToAgents(usage: SkillUsage | null): boolean {
  return !!usage?.usedByAgents?.length || !!usage?.otherAgentCount;
}

export function isRequiredByOtherSkills(usage: SkillUsage | null): boolean {
  return !!usage?.requiredBySkills?.length || !!usage?.otherSkillCount;
}

export function namesWithHidden(names: string[], hidden: number | undefined, hiddenLabel: (count: number) => string): string {
  return [...names, ...(hidden ? [hiddenLabel(hidden)] : [])].join(', ');
}
