export const normalizeUrl = (url: string): string => {
  if (!url || typeof url !== 'string') return '';
  const trimmed = String(url).trim();
  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed;
};
