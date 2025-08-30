import React from 'react';
import axios from 'src/utils/axios';
import UnifiedPermissionsDialog, {
  UnifiedPermissionsApi,
  UnifiedPermission,
  Team,
  User,
} from 'src/components/permissions/UnifiedPermissionsDialog';
import AgentApiService from '../../services/api';

interface AgentPermissionsDialogProps {
  open: boolean;
  onClose: () => void;
  agentId: string;
  agentName: string;
}

const makeAgentApi = (agentId: string): UnifiedPermissionsApi => ({
  loadPermissions: async () => {
    const list = await AgentApiService.listAgentPermissions(agentId);
    const mapped: UnifiedPermission[] = list.map((p: any) => ({
      id: p.id,
      userId: p.userId,
      type: p.type,
      name: p.name,
      email: p.email,
      role: p.role,
      createdAtTimestamp: p.createdAtTimestamp,
      updatedAtTimestamp: p.updatedAtTimestamp,
    }));
    return mapped;
  
  },
  loadUsers: async () => {
    const { data } = await axios.get(`/api/v1/users/graph/list`);
    const items = (data?.users || []) as any[];
    const users: User[] = items.map((u: any) => ({
      id: u.id,
      userId: u.userId,
      name: u.name,
      email: u.email,
    }));
    return users;
  },
  loadTeams: async () => {
    const { data } = await axios.get(`/api/v1/teams/list?limit=100`);
    const items = data?.teams || [];
    const teams: Team[] = items.map((t: any) => ({
      id: t.id,
      name: t.name,
      description: t.description,
    }));
    return teams;
  },
  createTeam: async ({ name, description, userIds, role }) => {
    const { data } = await axios.post('/api/v1/teams', {
      name,
      description,
      userIds,
      role,
    });
    const created = data?.data || data;
    return {
      id: created?._key,
      userId: created?.userId,
      name: created?.name,
      description: created?.description,
    } as Team;
  },
  createPermissions: async ({ userIds, teamIds, role }) => {
    await axios.post(`/api/v1/agents/${agentId}/share`, { userIds, teamIds, role });
  },
  updatePermissions: async ({ userIds, teamIds, role }) => {
    await axios.put(`/api/v1/agents/${agentId}/permissions`, { userIds, teamIds, role });
  },
  removePermissions: async ({ userIds, teamIds }) => {
    await axios.post(`/api/v1/agents/${agentId}/unshare`, { userIds: userIds || [], teamIds: teamIds || [] });
  },
});

const AgentPermissionsDialog: React.FC<AgentPermissionsDialogProps> = ({ open, onClose, agentId, agentName }) => {
  const api = React.useMemo(() => makeAgentApi(agentId), [agentId]);
  return (
    <UnifiedPermissionsDialog
      open={open}
      onClose={onClose}
      subjectName={agentName}
      api={api}
      title="Manage Access"
      addPeopleLabel="Add People"
    />
  );
};

export default AgentPermissionsDialog;


