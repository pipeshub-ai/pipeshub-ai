import React from 'react';
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

const makeKbApi = (kbId: string): UnifiedPermissionsApi => ({
  loadPermissions: async () => {
    const list = await KnowledgeBaseAPI.listKBPermissions(kbId);
    return list;
  },
  loadUsers: async () => {
    const { data } = await axios.get(`/api/v1/users/graph/list`);
    const items = (data?.users || []) as any[];
    const users :User[] = items;
    return users;
  },
  loadTeams: async () => {
    const { data } = await axios.get(`/api/v1/teams/list?limit=100`);
    const items = data?.teams || [];
    const teams :Team[] = items;
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
      name: created?.name,
      description: created?.description,
    } as Team;
  },
  createPermissions: async ({ userIds, teamIds, role }) => {
    const payload = {   
      userIds: userIds || [],
      teamIds: teamIds || [],
      role,
    };
    await KnowledgeBaseAPI.createKBPermissions(kbId, payload);
  },
  updatePermissions: async ({ userIds, teamIds, role }) => {
    const data = {
      userIds: userIds || [],
      teamIds: teamIds || [],
      role,
    };
    await KnowledgeBaseAPI.updateKBPermission(kbId, data);
  },
  removePermissions: async ({ userIds, teamIds }) => {
    const data = {
      userIds: userIds || [],
      teamIds: teamIds || [],
    };
    await KnowledgeBaseAPI.removeKBPermission(kbId, data);
  },
});

const KbPermissionsDialog: React.FC<KbPermissionsDialogProps> = ({ open, onClose, kbId, kbName }) => {
  const api = React.useMemo(() => makeKbApi(kbId), [kbId]);
  return (
    <UnifiedPermissionsDialog
      open={open}
      onClose={onClose}
      subjectName={kbName}
      api={api}
      title="Manage Access"
      addPeopleLabel="Add People"
    />
  );
};

export default KbPermissionsDialog;


