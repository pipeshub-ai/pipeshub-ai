/** The workspace page that lists everything in the Enterprise Edition. */
export const ENTERPRISE_OVERVIEW_ROUTE = '/workspace/enterprise';

const ENTERPRISE_CONTACT_URL = 'https://calendly.com/rishabh-pipeshub/30min';

/**
 * The "Talk with us" link (a call booking page), tagged with the page it was opened from so sales
 * can tell which page a booking came from. The tags carry the page's name only, nothing about the
 * person or organization.
 */
export function enterpriseContactUrl(source: string): string {
  const url = new URL(ENTERPRISE_CONTACT_URL);
  url.searchParams.set('utm_source', 'pipeshub-oss');
  url.searchParams.set('utm_medium', 'app');
  url.searchParams.set('utm_campaign', 'enterprise-edition');
  url.searchParams.set('utm_content', source);
  return url.toString();
}
