import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import { jwtDecode } from 'src/auth/context/jwt';
import { STORAGE_KEY } from 'src/auth/context/jwt/constant';

import type { UserData } from './types/user-data';
import type { OrganizationData } from './types/organization-data';
import type { AppUser, GroupUser, AppUserGroup } from './types/group-details';

interface PasswordChangeRequest {
  currentPassword: string;
  newPassword: string;
}

interface AddUsersToGroupsRequest {
  userIds: string[];
  groupIds: string[];
}

interface InviteUsersRequest {
  emails: string[];
  groupIds: string[];
}

interface createGroupProps {
  name: string;
  type: string;
}

export const getOrgById = async (orgId: string): Promise<OrganizationData> => {
  try {
    const response = await axios.get<OrganizationData>(`${CONFIG.backendUrl}/api/v1/org`);
    return response.data;
  } catch (error) {
    throw new Error('Error fetching organization');
  }
};

export const getUserById = async (userId: string | null): Promise<UserData> => {
  try {
    if (!userId) throw new Error('User ID is required');
    const response = await axios.get<UserData>(`${CONFIG.backendUrl}/api/v1/users/${userId}`);

    return response.data;
  } catch (error) {
    throw new Error('Error fetching user data');
  }
};

export const getOrgLogo = async (orgId: string): Promise<string | null> => {
  try {
    const response = await axios.get(`${CONFIG.backendUrl}/api/v1/org/logo`, {
      responseType: 'arraybuffer',
    });

    if (response.status === 204) {
      return null;
    }

    const contentType = response.headers['content-type'];
    if (contentType && contentType.includes('application/json')) {
      return null;
    }

    const blob = new Blob([response.data], { type: response.headers['content-type'] });

    return await new Promise<string | null>((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        // FileReader.result can be string | ArrayBuffer | null
        // We know it will be a string because we're using readAsDataURL
        resolve(reader.result as string);
      };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  } catch (error) {
    if (error.response && error.response.status === 404) {
      // Simply return null to indicate no logo exists
      return null;
    }
    throw new Error('Error fetching org logo');
  }
};

export const getUserLogo = async (userId: string): Promise<string | null> => {
  try {
    const response = await axios.get(`${CONFIG.backendUrl}/api/v1/users/${userId}/dp`, {
      responseType: 'arraybuffer',
    });

    const contentType = response.headers['content-type'];
    if (contentType && contentType.includes('application/json')) {
      return null;
    }

    const blob = new Blob([response.data], { type: response.headers['content-type'] });

    return await new Promise<string | null>((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        // FileReader.result can be string | ArrayBuffer | null
        // We know it will be a string because we're using readAsDataURL
        resolve(reader.result as string);
      };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  } catch (error) {
    throw new Error('Error fetching user logo');
  }
};

export const updateOrg = async (orgId: string, orgData: any) => {
  try {
    const response = await axios.put(`${CONFIG.backendUrl}/api/v1/org/`, orgData);
    return response.data.message;
  } catch (error) {
    throw new Error('Error updating org');
  }
};

export const changePassword = async ({ currentPassword, newPassword }: PasswordChangeRequest) => {
  try {
    const accessToken = localStorage.getItem(STORAGE_KEY);
    const response = await axios.post(
      `${CONFIG.authUrl}/api/v1/userAccount/password/reset`,
      {
        currentPassword,
        newPassword,
      },
      {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      }
    );
    return response.data;
  } catch (error) {
    throw new Error('Error updating password');
  }
};

export const updateUser = async (userId: string, userData: any) => {
  try {
    const response = await axios.put(`${CONFIG.backendUrl}/api/v1/users/${userId}`, userData);
    return response.data.message;
  } catch (error) {
    throw new Error('Error updating user');
  }
};

export const uploadOrgLogo = async (formData: any) => {
  try {
    const response = await axios.put(`${CONFIG.backendUrl}/api/v1/org/logo`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  } catch (error) {
    throw new Error('Error uplaoding org logo');
  }
};

export const uploadUserLogo = async (userId: string, formData: any) => {
  try {
    const response = await axios.put(`${CONFIG.backendUrl}/api/v1/users/${userId}/dp`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  } catch (error) {
    throw new Error('Error uploading user logo');
  }
};

export const deleteOrgLogo = async (orgId: string) => {
  try {
    const response = await axios.delete(`${CONFIG.backendUrl}/api/v1/org/logo`);
    return response.data;
  } catch (error) {
    throw new Error('Error deleting logo');
  }
};

export const deleteUserLogo = async (userId: string) => {
  try {
    const response = await axios.delete(`${CONFIG.backendUrl}/api/v1/users/${userId}/dp`);
    return response.data;
  } catch (error) {
    throw new Error('Error deleting User logo');
  }
};

export const getAllUsersWithGroups = async () => {
  try {
    const response = await axios.get<GroupUser[]>(
      `${CONFIG.backendUrl}/api/v1/users/fetch/with-groups`
    ); // Replace with the actual API endpoint

    return response.data;
  } catch (error) {
    throw new Error('Error fetching users');
  }
};

export const allGroups = async () => {
  try {
    const response = await axios.get<AppUserGroup[]>(`${CONFIG.backendUrl}/api/v1/userGroups`); // Replace with the actual API endpoint
    return response.data;
  } catch (error) {
    throw new Error('Error fetching groups');
  }
};

export const resendInvite = async (userId: string) => {
  try {
    const response = await axios.post(`${CONFIG.backendUrl}/api/v1/users/${userId}/resend-invite`);
    return response.data;
  } catch (error) {
    throw new Error('Error resending invite');
  }
};

export const removeUserFromGroup = async (userId: string, groupId: string | null) => {
  try {
    const response = await axios.post(`${CONFIG.backendUrl}/api/v1/userGroups/remove-users`, {
      userIds: [userId],
      groupIds: [groupId],
    });
    return response.data;
  } catch (error) {
    throw new Error('Error fetching group');
  }
};

export const removeUser = async (userId: string) => {
  try {
    const response = await axios.delete(`${CONFIG.backendUrl}/api/v1/users/${userId}`);
    return response.data;
  } catch (error) {
    throw new Error('Error deleting user');
  }
};

export const fetchGroupDetails = async (groupId: string | null) => {
  try {
    if (!groupId) {
      throw new Error('No group id found');
    }
    const response = await axios.get<AppUserGroup>(
      `${CONFIG.backendUrl}/api/v1/userGroups/${groupId}`
    ); // Adjust the URL if needed
    return response.data;
  } catch (error) {
    throw new Error('Error fetching group details');
  }
};

export const createGroup = async (groupData: createGroupProps) => {
  try {
    const response = await axios.post(`${CONFIG.backendUrl}/api/v1/userGroups`, groupData);
    return response.data;
  } catch (error) {
    throw new Error('Error creating groups');
  }
};

export const addUsersToGroups = async ({ userIds, groupIds }: AddUsersToGroupsRequest) => {
  try {
    const response = await axios.post(`${CONFIG.backendUrl}/api/v1/userGroups/add-users`, {
      userIds,
      groupIds,
    });
    return response.data;
  } catch (error) {
    throw new Error('Error adding users to groups');
  }
};

export const inviteUsers = async ({ emails, groupIds }: InviteUsersRequest) => {
  try {
    const response = await axios.post(`${CONFIG.backendUrl}/api/v1/users/bulk/invite`, {
      emails,
      groupIds,
    });
    return response.data;
  } catch (error) {
    throw new Error('Error inviting users to groups');
  }
};

export const deleteGroup = async (groupId: string) => {
  try {
    await axios.delete(`${CONFIG.backendUrl}/api/v1/userGroups/${groupId}`);
  } catch (error) {
    throw new Error('Error creating group');
  }
};

export const fetchAllUsers = async () => {
  try {
    const response = await axios.get<AppUser[]>(`${CONFIG.backendUrl}/api/v1/users`); // Adjust the URL if needed
    return response.data;
  } catch (error) {
    throw new Error('Error fetching users');
  }
};

export const getOrgIdFromToken = (): string => {
  const accessToken = localStorage.getItem(STORAGE_KEY);
  const decodedToken = jwtDecode(accessToken);
  const { orgId } = decodedToken;
  return orgId;
};

export const getUserIdFromToken = (): string => {
  const accessToken = localStorage.getItem(STORAGE_KEY);
  const decodedToken = jwtDecode(accessToken);
  const { userId } = decodedToken;
  return userId;
};

export const logout = async (): Promise<void> => {
  try {
    // Simply remove the JWT token from localStorage
    localStorage.removeItem(STORAGE_KEY);

    // Refresh the page
    window.location.reload();
  } catch (error) {
    console.error('Error during logout:', error);
    throw new Error(
      `Unable to logout: ${error instanceof Error ? error.message : 'Unknown error'}`
    );
  }
};

export const updateDataCollectionConsent = async (enableMetricCollection: boolean) => {
  try {
    const response = await axios.put(
      `${CONFIG.backendUrl}/api/v1/configurationManager/metricsCollection/toggle`,
      {
        enableMetricCollection,
      }
    );
    return response.data;
  } catch (error) {
    throw new Error('Error editing the consent');
  }
};

export const getDataCollectionConsent = async (): Promise<boolean> => {
  try {
    const response = await axios.get(
      `${CONFIG.backendUrl}/api/v1/configurationManager/metricsCollection`
    );
    if (
      response.data.enableMetricCollection === 'true' ||
      response.data.enableMetricCollection === true
    )
      return true;
    return false;
  } catch (error) {
    throw new Error('Error editing the consent');
  }
};
