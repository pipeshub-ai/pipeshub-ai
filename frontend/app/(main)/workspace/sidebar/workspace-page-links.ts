/**
 * Workspace pages a build adds that shared views link to, by name (`workspacePageLinks` in config.ts). This build
 * adds none, so a view that would link to one shows no link: never a link to a page that is not there.
 */
export const workspacePageLinks: Readonly<Partial<Record<string, string>>> = Object.freeze({});
