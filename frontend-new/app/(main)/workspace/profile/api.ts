import { apiClient } from '@/lib/api';

// JWT token helpers are in lib/utils — re-exported for convenience
export { getUserIdFromToken, getUserEmailFromToken, getAccountTypeFromToken } from '@/lib/utils/jwt';

const USERS_URL = '/api/v1/users';
const AUTH_URL = '/api/v1/userAccount';

// ========================================
// Types
// ========================================

export interface UserData {
  _id: string;
  orgId: string;
  fullName: string;
  firstName?: string;
  lastName?: string;
  designation?: string;
  email?: string;
  hasLoggedIn: boolean;
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
  slug?: string;
}

export interface UpdateUserPayload {
  fullName: string;
  firstName?: string;
  lastName?: string;
  designation?: string;
  email?: string;
}

export interface ChangePasswordPayload {
  currentPassword: string;
  newPassword: string;
}

// ========================================
// API
// ========================================

export const ProfileApi = {
  /** GET /api/v1/users/{userId} */
  async getUser(userId: string): Promise<UserData> {
    const { data } = await apiClient.get<UserData>(`${USERS_URL}/${userId}`);
    return data;
  },

  /** GET /api/v1/users/{userId}/email */
  async getUserEmail(userId: string): Promise<string | null> {
    try {
      const { data } = await apiClient.get<{ email: string }>(
        `${USERS_URL}/${userId}/email`
      );
      return data.email ?? null;
    } catch {
      return null;
    }
  },

  /** PUT /api/v1/users/{userId} */
  async updateUser(userId: string, payload: UpdateUserPayload): Promise<void> {
    await apiClient.put(`${USERS_URL}/${userId}`, payload);
  },

  /** GET /api/v1/users/dp — download avatar and return a data URL */
  async getAvatar(userId?: string | null): Promise<string | null> {
    if (!userId) return null;
    try {
      const response = await apiClient.get<ArrayBuffer>(`${USERS_URL}/dp`, {
        responseType: 'arraybuffer',
        headers: { Accept: 'image/*, */*' },
      });
      const contentType = (response.headers as Record<string, string>)['content-type'];
      if (!contentType || contentType.includes('application/json') || contentType.includes('text/html')) return null;
      const blob = new Blob([response.data as ArrayBuffer], { type: contentType });
      return await new Promise<string | null>((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    } catch {
      return null;
    }
  },

  /** PUT /api/v1/users/dp — upload avatar (multipart/form-data, field: 'file') */
  async uploadAvatar(userId: string, file: File): Promise<string | null> {
    const formData = new FormData();
    formData.append('file', file);
    await apiClient.put(`${USERS_URL}/dp`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    // Fetch back the processed image from server (EXIF stripped, compressed)
    return this.getAvatar(userId);
  },

  /** DELETE /api/v1/users/dp — user resolved from JWT */
  async deleteAvatar(): Promise<void> {
    await apiClient.delete(`${USERS_URL}/dp`);
  },

  // ─────────────────────────────────────────────────────────────────────────
  // Change Password — used by the profile change-password UI flow
  // ─────────────────────────────────────────────────────────────────────────

  /** POST /api/v1/userAccount/password/reset */
  async changePassword(payload: ChangePasswordPayload): Promise<void> {
    await apiClient.post(`${AUTH_URL}/password/reset`, payload);
  },


  // ─────────────────────────────────────────────────────────────────────────
  // Change Email verification flow
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * STUB: Checks whether `newEmail` is tied to an existing account.
   * Replace with a real API call (e.g. GET /api/v1/users/check-email?email=…)
   * when the backend endpoint is available.
   */
  async checkEmailExists(_newEmail: string): Promise<{ exists: boolean }> {
    // Stub always returns { exists: true } so the "found" state is reachable.
    return { exists: true };
  },

  /**
   * PUT /api/v1/users/{userId} with `{ email }` — backend sends the verification
   * link to the new address (see users.controller `updateUser` → `emailChange`).
   */
  async sendEmailVerificationLink(userId: string, newEmail: string): Promise<void> {
    const { data } = await apiClient.put<
      UserData & { meta?: { emailChangeMailStatus?: 'notNeeded' | 'sent' | 'failed' } }
    >(`${USERS_URL}/${userId}`, { email: newEmail });
    if (data.meta?.emailChangeMailStatus === 'failed') {
      throw new Error('Failed to send verification email');
    }
  },
};
