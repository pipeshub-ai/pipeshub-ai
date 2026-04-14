import { apiClient } from '@/lib/api';
import type {
  Team,
  TeamMember,
  TeamsListResponse,
  CreateTeamPayload,
  UpdateTeamPayload,
} from './types';

const BASE_URL = '/api/v1/teams';

export const TeamsApi = {
  /**
   * List teams for the current user.
   * GET /api/v1/teams/user/teams
   * Supports pagination and search via query params.
   */
  async listTeams(params?: {
    page?: number;
    limit?: number;
    search?: string;
  }): Promise<{ teams: Team[]; totalCount: number }> {
    const { data } = await apiClient.get<TeamsListResponse>(
      `${BASE_URL}/user/teams`,
      { params }
    );
    const teams = data.teams ?? [];
    return {
      teams,
      totalCount: data.pagination?.total ?? teams.length,
    };
  },

  /**
   * Get a single team by ID.
   * GET /api/v1/teams/:id (UUID)
   */
  async getTeam(id: string): Promise<Team> {
    const { data } = await apiClient.get<{ team: Team } | Team>(`${BASE_URL}/${id}`);
    if (data && typeof data === 'object' && 'team' in data) {
      return (data as { team: Team }).team;
    }
    return data as Team;
  },

  /**
   * Create a new team.
   * POST /api/v1/teams
   * Body: { name, description?, userRoles?: [{ userId (UUID), role }] }
   */
  async createTeam(payload: CreateTeamPayload): Promise<Team> {
    const { data } = await apiClient.post<{ team: Team } | Team>(BASE_URL, payload);
    if (data && typeof data === 'object' && 'team' in data) {
      return (data as { team: Team }).team;
    }
    return data as Team;
  },

  /**
   * Update an existing team.
   * PUT /api/v1/teams/:id (UUID)
   * Body: { name?, description?, updateUserRoles?: [{ userId (UUID), role }] }
   */
  async updateTeam(id: string, payload: UpdateTeamPayload): Promise<Team> {
    const { data } = await apiClient.put<{ team: Team } | Team>(`${BASE_URL}/${id}`, payload);
    if (data && typeof data === 'object' && 'team' in data) {
      return (data as { team: Team }).team;
    }
    return data as Team;
  },

  /**
   * Get team members with profile pictures.
   * GET /api/v1/teams/:teamId/users
   */
  async getTeamUsers(teamId: string): Promise<TeamMember[]> {
    const { data } = await apiClient.get<{ members: TeamMember[] }>(
      `${BASE_URL}/${teamId}/users`
    );
    return data?.members ?? [];
  },

  /**
   * Delete a team.
   * DELETE /api/v1/teams/:id (UUID)
   */
  async deleteTeam(id: string): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${id}`);
  },
};
