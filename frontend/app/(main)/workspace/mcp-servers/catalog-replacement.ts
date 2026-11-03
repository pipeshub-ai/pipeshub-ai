import type { McpServerTemplate } from './types';

/** A replaced catalog entry makes no new servers; the ones already made from it keep running. */
export function isOfferedForNewServers(template: Pick<McpServerTemplate, 'replacedBy'>): boolean {
  return !template.replacedBy;
}

/** The entry that took `template`'s place, when the catalog has it. */
export function replacementFor(
  template: Pick<McpServerTemplate, 'replacedBy'>,
  templates: McpServerTemplate[]
): McpServerTemplate | null {
  if (!template.replacedBy) return null;
  return templates.find((candidate) => candidate.typeId === template.replacedBy) ?? null;
}

/** What a person can set up a server of their own from: remote entries that still make new servers.
 * A local command (STDIO) runs on PipesHub's machine, which only an administrator may allow. */
export function offeredForPersonalServers(templates: McpServerTemplate[]): McpServerTemplate[] {
  return templates.filter((tpl) => tpl.transport !== 'stdio' && isOfferedForNewServers(tpl));
}
