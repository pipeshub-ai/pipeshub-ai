import type { PolicyLayer } from './policy-alias';

/**
 * Allowed websites (WP-63): the named sites an agent's sandbox may reach
 * through the egress proxy, as the org's live layer carries them. Off by
 * default: with no layer that lists a site, a sandbox reaches no website.
 */
export const WEBSITES_SECTION = 'websites';

export type WebsitePolicy = NonNullable<PolicyLayer['websites']>;
export type AllowedWebsite = WebsitePolicy['sites'][number];
export type WebsiteAccess = AllowedWebsite['access'];
