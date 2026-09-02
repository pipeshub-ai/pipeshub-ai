'use client';
export { default as Link } from 'next/link';
export const withCurrentOrgId = (href: string): string => href;
export function useOrgHref(href: string | undefined): string | undefined { return href; }
export const OrgUrlCleaner = (): null => null;