import { apiClient } from '@/lib/api';
import type {
  Team,
  TeamMember,
  TeamsListResponse,
  CreateTeamPayload,
  UpdateTeamPayload,
} from './types';

const BASE_URL = '/api/v1/teams';

/**
 * The team inside a team-service response: create answers `{ data: team }`,
 * get and update answer `{ team }`, and older proxies answered the team bare.
 */
export function unwrapTeam(body: unknown): Team {
  if (body && typeof body === 'object') {
    const { team, data } = body as { team?: unknown; data?: unknown };
    const inner = team ?? data;
    if (inner && typeof inner === 'object') return inner as Team;
  }
  return body as Team;
}

export const TeamsApi = {
  /**
   * List teams for the current user.
   * GET /api/v1/teams/user/teams
   * Supports pagination and search via query params.
   * `created_by` is the creator's Mongo userId (resolved to graph key on the server).
   */
  async listTeams(params?: {
    page?: number;
    limit?: number;
    search?: string;
    /** Mongo userId of team creator */
    created_by?: string;
    created_after?: number;
    created_before?: number;
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
    // A team the caller is not in answers 404; every caller already falls back
    // (unnamed share row, teams list), so a toast would only be noise.
    const { data } = await apiClient.get<unknown>(`${BASE_URL}/${id}`, {
      suppressErrorToast: (error) => error.statusCode === 404,
    });
    return unwrapTeam(data);
  },

  /**
   * Create a new team.
   * POST /api/v1/teams
   * Body: { name, description?, userRoles?: [{ userId (UUID), role }] }
   */
  async createTeam(payload: CreateTeamPayload): Promise<Team> {
    // The form shows the server's reason itself (e.g. a name already taken).
    const { data } = await apiClient.post<unknown>(BASE_URL, payload, {
      suppressErrorToast: true,
    });
    return unwrapTeam(data);
  },

  /**
   * Update an existing team.
   * PUT /api/v1/teams/:id
   * Body: { name?, description?, addUserRoles?: [{ userId (Mongo), role }],
   *   removeUserIds?: [Mongo userId], updateUserRoles?: [{ userId (Mongo), role }] }
   */
  async updateTeam(id: string, payload: UpdateTeamPayload): Promise<Team> {
    const { data } = await apiClient.put<unknown>(
      `${BASE_URL}/${id}`,
      payload,
      { suppressErrorToast: true }
    );
    return unwrapTeam(data);
  },

  /**
   * Get team members with profile pictures (paginated).
   * GET /api/v1/teams/:teamId/users?page=&limit=&search=
   */
  async getTeamUsers(
    teamId: string,
    params?: { page?: number; limit?: number; search?: string }
  ): Promise<{ members: TeamMember[]; totalCount: number }> {
    const { data } = await apiClient.get<{
      team?: { members?: TeamMember[]; memberCount?: number };
      pagination?: { totalCount?: number };
    }>(
      `${BASE_URL}/${teamId}/users`,
      { params }
    );
    const members = (data?.team?.members ?? []).filter(
      (m) => typeof m.userId === 'string' && m.userId.trim().length > 0
    );
    const totalCount = data?.pagination?.totalCount ?? data?.team?.memberCount ?? members.length;
    return { members, totalCount };
  },

  /**
   * Delete a team.
   * DELETE /api/v1/teams/:id (UUID)
   */
  async deleteTeam(id: string): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${id}`);
  },
};
