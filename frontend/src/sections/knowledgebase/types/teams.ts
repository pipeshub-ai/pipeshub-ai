export interface Team {
    id: string;
    name: string;
    description?: string;
    createdBy: string;
    orgId: string;
    createdAtTimestamp: number;
    updatedAtTimestamp: number;
    members: TeamMember[];
    memberCount: number;
    canEdit: boolean;
    canDelete: boolean;
    canManageMembers: boolean;
    currentUserPermission?: {
      _key: string;
      _id: string;
      _from: string;
      _to: string;
      _rev: string;
      type: string;
      role: string;
      createdAtTimestamp: number;
      updatedAtTimestamp: number;
    };
  }
  
  export interface TeamMember {
    id: string;
    userId: string;
    userName: string;
    userEmail: string;
    role: string;
    joinedAt: number;
    isOwner: boolean;
  }
  
  export interface User {
    _id: string;
    _key: string;
    fullName: string;
    email: string;
    id?: string;
    name?: string;
    userId?: string;
    isActive?: boolean;
    createdAtTimestamp?: number;
    updatedAtTimestamp?: number;
  }
  
  export interface UserRole {
    userId: string;
    role: TeamRole;
  }

  export interface TeamFormData {
    name: string;
    description?: string;
    role: string; // Default role for backward compatibility
    members: User[];
    memberRoles?: UserRole[]; // Individual roles for each member
  }
  
  export type TeamRole = 'READER' | 'WRITER' | 'OWNER';
  
  export interface RoleOption {
    value: TeamRole;
    label: string;
    description: string;
  }