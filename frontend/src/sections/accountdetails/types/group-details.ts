import type { AlertColor } from '@mui/material';


export interface AppUserGroup {
  _id: string;
  name: string;
  type: string;
  orgId: string;
  users: string[];
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
  slug: string;
  __v: number;
}

export interface AppUser {
  _id: string;
  orgId: string;
  fullName: string;
  email: string;
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
  slug: string;
  __v: number;
  designation?: string;
  firstName?: string;
  lastName?: string;
  deletedBy?: string;
  isEmailVerified: boolean;
}

export interface GroupUser {
  _id: string;
  fullName: string;
  email: string;
  orgId: string;
  hasLoggedIn: boolean;
  groups: {
    name: string;
    type: string;
  }[];

}

export interface SnackbarState {
  open: boolean;
  message: string;
  severity: AlertColor;
}

export interface EditGroupModalProps {
  open: boolean;
  onClose: () => void;
  groupId: string | null;
  groupName: string;
}

export interface AddUsersToGroupsModalProps {
  open: boolean;
  onClose: () => void;
  onUsersAdded: () => void;
  allUsers: GroupUser[] | null;
  group: string | null;
}

export interface AddUserModalProps {
  open: boolean;
  onClose: () => void;
  groups: AppUserGroup[];
  onUsersAdded: (message: string) => void;
}