import type { WorkspaceNavExtra } from './workspace-nav';

/** Entries an edition adds to the workspace sidebar. Editions plug in via config.ts; this default adds none. */
export const workspaceNavExtras: readonly WorkspaceNavExtra[] = [];
