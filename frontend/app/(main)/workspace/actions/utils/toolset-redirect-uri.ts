/** Public redirect URL for toolset OAuth (register at the IdP). */
export function toolsetRedirectUri(origin: string, toolsetType: string): string {
  const slug = encodeURIComponent(toolsetType.trim());
  return `${origin.replace(/\/+$/, '')}/toolsets/oauth/callback/${slug}`;
}
