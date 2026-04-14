import { useAuthStore } from '@/lib/store/auth-store';

// ========================================
// JWT token helpers
// ========================================

/** Decodes the JWT payload without verifying the signature */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const base64Url = token.split('.')[1];
    if (!base64Url) return null;
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    // JWT base64url payloads may not be a multiple of 4 chars; pad to avoid atob DOMException
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
    const json = decodeURIComponent(
      atob(padded)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    return JSON.parse(json);
  } catch {
    return null;
  }
}

export function getUserIdFromToken(): string | null {
  const token = useAuthStore.getState().accessToken;
  if (!token) return null;
  const payload = decodeJwtPayload(token);
  return (payload?.userId as string) || null;
}

export function getUserEmailFromToken(): string | null {
  const token = useAuthStore.getState().accessToken;
  if (!token) return null;
  const payload = decodeJwtPayload(token);
  return (payload?.email as string) || null;
}

export function getAccountTypeFromToken(): string | null {
  const token = useAuthStore.getState().accessToken;
  if (!token) return null;
  const payload = decodeJwtPayload(token);
  return (payload?.accountType as string) || null;
}
