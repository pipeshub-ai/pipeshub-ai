import type { TFunction } from 'i18next';

/**
 * The one rule for what a group or a team may be called, and how long its
 * description may be. The server enforces the same limits (a team and a group
 * name are 1 to 100 characters after trimming; a description is at most 500).
 */
export const WORKSPACE_NAME_MAX_LENGTH = 100;
export const WORKSPACE_DESCRIPTION_MAX_LENGTH = 500;

/** The problem with a group or team name, or undefined when it is fine. */
export function workspaceNameProblem(value: string, t: TFunction): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return t('workspace.common.validation.nameRequired');
  if (trimmed.length > WORKSPACE_NAME_MAX_LENGTH) {
    return t('workspace.common.validation.nameTooLong', { max: WORKSPACE_NAME_MAX_LENGTH });
  }
  return undefined;
}

/** The problem with a group or team description (optional), or undefined. */
export function workspaceDescriptionProblem(value: string, t: TFunction): string | undefined {
  if (value.trim().length > WORKSPACE_DESCRIPTION_MAX_LENGTH) {
    return t('workspace.common.validation.descriptionTooLong', {
      max: WORKSPACE_DESCRIPTION_MAX_LENGTH,
    });
  }
  return undefined;
}
