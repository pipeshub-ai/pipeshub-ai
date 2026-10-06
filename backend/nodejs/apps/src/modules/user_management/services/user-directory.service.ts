import mongoose from 'mongoose';
import { Users, UserKind } from '../schema/users.schema';

export interface DirectoryUser {
  userId: string;
  displayName: string;
  email?: string;
  kind: UserKind;
  isDisabled: boolean;
}

export interface DisplayNameOptions {
  /** Default true. False leaves a user with no name as '' so an address never lands in an email or other outbound text. */
  emailFallback?: boolean;
}

export interface IUserDirectory {
  /** Maps every valid requested id to its display name; '' when the user is not in the org. */
  displayNames(
    orgId: string,
    userIds: readonly string[],
    options?: DisplayNameOptions,
  ): Promise<ReadonlyMap<string, string>>;
  findByIds(
    orgId: string,
    userIds: readonly string[],
  ): Promise<readonly DirectoryUser[]>;
}

export function displayNameOf(
  user: {
    fullName?: string;
    firstName?: string;
    lastName?: string;
    email?: string;
  },
  options: DisplayNameOptions = {},
): string {
  const fullName = user.fullName?.trim();
  if (fullName) return fullName;
  const parts = [user.firstName, user.lastName]
    .filter((part): part is string => Boolean(part?.trim()))
    .join(' ')
    .trim();
  if (parts) return parts;
  return options.emailFallback === false ? '' : (user.email?.trim() ?? '');
}

function validUniqueIds(userIds: readonly string[]): string[] {
  return [...new Set(userIds)].filter((id) =>
    mongoose.Types.ObjectId.isValid(id),
  );
}

export class MongoUserDirectory implements IUserDirectory {
  async displayNames(
    orgId: string,
    userIds: readonly string[],
    options: DisplayNameOptions = {},
  ): Promise<ReadonlyMap<string, string>> {
    const ids = validUniqueIds(userIds);
    const names = new Map<string, string>(ids.map((id) => [id, '']));
    if (ids.length === 0) return names;

    const users = await Users.find({
      orgId: new mongoose.Types.ObjectId(orgId),
      isDeleted: false,
      _id: { $in: ids.map((id) => new mongoose.Types.ObjectId(id)) },
    })
      .select(
        options.emailFallback === false
          ? 'fullName firstName lastName'
          : 'fullName firstName lastName email',
      )
      .lean()
      .exec();

    for (const user of users) {
      names.set(user._id.toString(), displayNameOf(user, options));
    }
    return names;
  }

  async findByIds(
    orgId: string,
    userIds: readonly string[],
  ): Promise<readonly DirectoryUser[]> {
    const ids = validUniqueIds(userIds);
    if (ids.length === 0) return [];

    const users = await Users.find({
      orgId: new mongoose.Types.ObjectId(orgId),
      isDeleted: false,
      _id: { $in: ids.map((id) => new mongoose.Types.ObjectId(id)) },
    })
      .select('fullName firstName lastName email kind isDisabled')
      .lean()
      .exec();

    return users.map((user) => ({
      userId: user._id.toString(),
      displayName: displayNameOf(user),
      ...(user.email ? { email: user.email } : {}),
      kind: user.kind ?? 'human',
      isDisabled: user.isDisabled === true,
    }));
  }
}
