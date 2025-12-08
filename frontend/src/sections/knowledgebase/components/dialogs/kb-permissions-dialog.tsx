import React, { useRef } from 'react';
import axios from 'src/utils/axios';
import UnifiedPermissionsDialog, {
  UnifiedPermissionsApi,
  Team,
  User,
} from 'src/components/permissions/UnifiedPermissionsDialog';
import { KnowledgeBaseAPI } from '../../services/api';

interface KbPermissionsDialogProps {
  open: boolean;
  onClose: () => void;
  kbId: string;
  kbName: string;
}

const makeKbApi = (kbId: string, reloadTeamsRef: React.MutableRefObject<(() => Promise<Team[]>) | null>): UnifiedPermissionsApi => ({
  loadPermissions: async () => {
    const list = await KnowledgeBaseAPI.listKBPermissions(kbId);
    return list;
  },
  loadUsers: async () => {
    const { data } = await axios.get(`/api/v1/users/graph/list`);
    const items = (data?.users || []) as any[];
    // Normalize user objects to match expected format
    const users: User[] = items.map((item: any) => ({
      id: item.id || item._key || item._id || '',
      userId: item.userId || item.id || '',
      name: item.name || item.fullName || item.userName || '',
      email: item.email || '',
      isActive: item.isActive !== false,
    }));
    return users;
  },
  loadTeams: async () => {
    // Load all teams the user is a member of
    const { data } = await axios.get(`/api/v1/teams/user/teams?limit=100`);
    const items = data?.teams || [];
    // Normalize team objects to match expected format
    const teams: Team[] = items.map((item: any) => ({
      id: item.id || item._key || '',
      name: item.name || '',
      description: item.description || '',
      createdBy: item.createdBy || '',
      createdAtTimestamp: item.createdAtTimestamp || Date.now(),
      updatedAtTimestamp: item.updatedAtTimestamp || Date.now(),
      members: item.members || [],
      memberCount: item.memberCount || 0,
    }));
    return teams;
  },
  createTeam: async ({ name, description, userIds, role, memberRoles }) => {
    try {
      // Filter out any empty, null, or undefined values from userIds
      const validUserIds = (userIds || []).filter(
        (id: any) => id != null && typeof id === 'string' && id.trim() !== ''
      );

      const body: any = {
        name: name.trim(),
      };

      if (description && description.trim()) {
        body.description = description.trim();
      }

      // Use new format with individual user roles if provided, otherwise use legacy format
      if (memberRoles && memberRoles.length > 0) {
        body.userRoles = memberRoles;
      } else if (validUserIds.length > 0) {
        // Legacy format: single role for all users
        body.userIds = validUserIds;
        body.role = role || 'READER';
      }

      const { data } = await axios.post('/api/v1/teams', body);
      
      // Handle different response structures
      // Python backend returns: { status: "success", message: "...", data: team }
      // Node.js might return: { status: "success", data: { status: "success", data: team } }
      let created = data;
      
      // Navigate through nested response structures
      if (created?.data) {
        created = created.data;
        // If still nested (Node.js proxy case)
        if (created?.data && typeof created.data === 'object') {
          created = created.data;
        }
      }
      
      // Extract team ID - try multiple possible fields
      const teamId = created?.id || created?._key || created?._id || '';
      
      if (!teamId) {
        console.error('Team creation response:', data);
        throw new Error('Failed to get team ID from creation response. Please try again.');
      }

      const team: Team = {
        id: teamId,
        name: created?.name || name,
        description: created?.description || description || '',
        createdBy: created?.createdBy || '',
        createdAtTimestamp: created?.createdAtTimestamp || Date.now(),
        updatedAtTimestamp: created?.updatedAtTimestamp || Date.now(),
        members: created?.members || [],
        memberCount: created?.memberCount || created?.members?.length || 0,
      };

      // Reload teams list after creation to ensure consistency
      if (reloadTeamsRef.current) {
        await reloadTeamsRef.current();
      }

      return team;
    } catch (error: any) {
      console.error('Error creating team:', error);
      throw new Error(error.response?.data?.message || error.message || 'Failed to create team');
    }
  },
  createPermissions: async ({ userIds, teamIds, role }) => {
    const payload: any = {   
      userIds: (userIds || []).filter((id: any) => id && id.trim() !== ''),
      teamIds: (teamIds || []).filter((id: any) => id && id.trim() !== ''),
    };
    // Role is only required for users, not teams
    if (userIds && userIds.length > 0 && role) {
      payload.role = role;
    }
    await KnowledgeBaseAPI.createKBPermissions(kbId, payload);
  },
  updatePermissions: async ({ userIds, teamIds, role }) => {
    const data = {
      userIds: (userIds || []).filter((id: any) => id && id.trim() !== ''),
      teamIds: (teamIds || []).filter((id: any) => id && id.trim() !== ''),
      role,
    };
    await KnowledgeBaseAPI.updateKBPermission(kbId, data);
  },
  removePermissions: async ({ userIds, teamIds }) => {
    const data = {
      userIds: (userIds || []).filter((id: any) => id && id.trim() !== ''),
      teamIds: (teamIds || []).filter((id: any) => id && id.trim() !== ''),
    };
    await KnowledgeBaseAPI.removeKBPermission(kbId, data);
  },
});

const KbPermissionsDialog: React.FC<KbPermissionsDialogProps> = ({ open, onClose, kbId, kbName }) => {
  const reloadTeamsRef = useRef<(() => Promise<Team[]>) | null>(null);
  
  const api = React.useMemo(() => {
    const apiInstance = makeKbApi(kbId, reloadTeamsRef);
    
    // Store the loadTeams function so createTeam can call it
    reloadTeamsRef.current = apiInstance.loadTeams;
    
    return apiInstance;
  }, [kbId]);

  return (
    <UnifiedPermissionsDialog
      open={open}
      onClose={onClose}
      subjectName={kbName}
      api={api}
      title="Manage Access"
      addPeopleLabel="Add People & Teams"
    />
  );
};

export default KbPermissionsDialog;


