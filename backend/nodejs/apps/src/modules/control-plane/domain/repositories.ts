import type { PolicyLayer } from './policy-alias';

/**
 * Allowed git hosts for `repository_fetch` (WP-63 part A), as the org's live
 * layer carries them. A git host is never a website: the worker fetches a
 * repository outside the sandbox after the person allows it. With no layer
 * that lists hosts, a run with web access gets the release's public presets
 * (ADR-REPO-01).
 */
export const REPOSITORIES_SECTION = 'repositories';

export type RepositoryPolicy = NonNullable<PolicyLayer['repositories']>;
export type AllowedRepositoryHost = RepositoryPolicy['hosts'][number];
