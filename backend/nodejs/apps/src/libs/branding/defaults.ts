export interface BrandConfig {
  productName: string;
  supportEmail: string;
  /** Where a person whose account is disabled is sent; the support address when unset. */
  accountContactEmail?: string;
  privacyUrl: string | null;
  websiteUrl: string | null;
  footerBlurb: string;
}

export const DEFAULT_BRAND: BrandConfig = {
  productName: 'PipesHub',
  supportEmail: 'support@pipeshub.com',
  accountContactEmail: 'contact@pipeshub.com',
  privacyUrl: 'https://pipeshub.com/privacy-policy',
  websiteUrl: 'https://pipeshub.com',
  footerBlurb:
    'Where your team and AI work together. PipesHub is an open-source context layer for AI agents and an enterprise search platform, with permission-aware, cited answers across your work apps.',
};
