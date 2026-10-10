import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { User } from '@/app/(main)/workspace/users/types';
import type { ShareUser } from './types';

/** Map merged Mongo users to ShareUser with Mongo `userId` as `id`. */
export function toShareUsers(users?: User[]): ShareUser[] {
  return (users ?? []).map((u) => ({
    id: u.userId,
    name: u.name ?? u.email ?? '',
    email: u.email,
    avatarUrl: u.profilePicture,
    isInOrg: true,
  }));
}

export interface ShareUsersPaginatedParams {
  page: number;
  limit: number;
  search?: string;
}

/** Paginated org user list via GET /api/v1/users (Mongo-backed). `exclude` drops users an adapter cannot share with. */
export async function fetchShareUsersPaginated(
  params: ShareUsersPaginatedParams,
  options?: { exclude?: (user: User) => boolean },
): Promise<{ users: ShareUser[]; totalCount: number }> {
  const result = await UsersApi.fetchMergedUsers({
    page: params.page,
    limit: params.limit,
    search: params.search,
  });
  return {
    users: toShareUsers(options?.exclude ? result.users.filter((u) => !options.exclude!(u)) : result.users),
    totalCount: result.totalCount,
  };
}
