import { useState, useCallback } from 'react';

import axios from 'src/utils/axios';
import { CONFIG } from 'src/config-global';

interface UseUserEmailsOptions {
  onError?: (error: unknown, userId: string) => void;
}

/**
 * Custom hook for managing user email fetching
 * Provides state and functions to fetch and cache user emails on demand
 * 
 * @param options - Optional configuration
 * @param options.onError - Callback function called when email fetch fails
 * 
 * @returns Object containing:
 *   - userEmails: Record of userId -> email mappings
 *   - emailLoading: Record of userId -> loading state
 *   - fetchUserEmail: Function to fetch email for a user (returns Promise<string | null>)
 */
export function useUserEmails(options?: UseUserEmailsOptions) {
  const [emailLoading, setEmailLoading] = useState<Record<string, boolean>>({});
  const [userEmails, setUserEmails] = useState<Record<string, string>>({});

  const fetchUserEmail = useCallback(
    async (userIdentifier: string): Promise<string | null> => {
      // If email is already fetched, return it
      if (userEmails[userIdentifier]) {
        return userEmails[userIdentifier];
      }

      setEmailLoading((prev) => ({ ...prev, [userIdentifier]: true }));
      try {
        const response = await axios.get<{ email: string }>(
          `${CONFIG.backendUrl}/api/v1/users/${userIdentifier}/email`
        );
        const email = response.data.email;
        setUserEmails((prev) => ({ ...prev, [userIdentifier]: email }));
        return email;
      } catch (error) {
        console.error('Failed to fetch email:', error);
        if (options?.onError) {
          options.onError(error, userIdentifier);
        }
        return null;
      } finally {
        setEmailLoading((prev) => ({ ...prev, [userIdentifier]: false }));
      }
    },
    [userEmails, options]
  );

  return {
    userEmails,
    emailLoading,
    fetchUserEmail,
  };
}

