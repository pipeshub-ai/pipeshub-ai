// OSS (Community Edition) — single-org, so hrefs and URLs pass through untouched.
export { default as Link } from 'next/link';
export const withCurrentOrgId = (href: string): string => href;
export const OrgUrlCleaner = (): null => null;
export const useOrgHref = (href: string | undefined): string | undefined => href;
