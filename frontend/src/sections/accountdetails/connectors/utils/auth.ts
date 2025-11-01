export const isNoneAuthType = (authType?: string | null): boolean =>
  (authType || '').toUpperCase() === 'NONE';
