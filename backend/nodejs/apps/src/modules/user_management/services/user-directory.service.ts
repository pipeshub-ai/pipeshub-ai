import mongoose from 'mongoose';
import { Users, UserKind } from '../schema/users.schema';

export interface DirectoryUser {
  userId: string;
  displayName: string;
  email?: string;
  /** The name parts a picker matches typed text against. */
  firstName?: string;
  middleName?: string;
  lastName?: string;
  fullName?: string;
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
  /**
   * Active human members of the org whose first, middle or last name, any word of the full name, or
   * email starts with every whitespace-separated token of `q`. Never another org's, disabled, deleted
   * or service users.
   */
  searchOrgMembers(
    orgId: string,
    q: string,
    limit: number,
  ): Promise<readonly DirectoryUser[]>;
}

export const USER_SEARCH_MAX_TOKENS = 5;
export const USER_SEARCH_MAX_TOKEN_LENGTH = 64;

/** The query's tokens, lowercased, capped in number and length. */
export function searchTokens(q: string): string[] {
  return q
    .toLowerCase()
    .split(/\s+/u)
    .filter((t) => t !== '')
    .slice(0, USER_SEARCH_MAX_TOKENS)
    .map((t) => t.slice(0, USER_SEARCH_MAX_TOKEN_LENGTH));
}

const escapeRegex = (text: string): string =>
  text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

interface NameFields {
  fullName?: string;
  firstName?: string;
  middleName?: string;
  lastName?: string;
  email?: string;
}

/** Same rule as `searchOrgMembers`, for people already at hand: every token prefixes some name part, full-name word or the email. */
export function matchesSearchTokens(
  user: NameFields,
  tokens: readonly string[],
): boolean {
  const prefixes = [
    user.firstName,
    user.middleName,
    user.lastName,
    user.email,
    ...(user.fullName ?? '').split(/\s+/u),
  ].map((v) => (v ?? '').toLowerCase());
  return tokens.every((t) => prefixes.some((p) => p !== '' && p.startsWith(t)));
}

/** Anchored, escaped regexes only: typed text is never a pattern. */
function searchFilter(tokens: readonly string[]): Record<string, unknown>[] {
  return tokens.map((t) => {
    const word = new RegExp(`^${escapeRegex(t)}`, 'i');
    return {
      $or: [
        { firstName: word },
        { middleName: word },
        { lastName: word },
        { email: word },
        { fullName: new RegExp(`(^|\\s)${escapeRegex(t)}`, 'i') },
      ],
    };
  });
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

const DIRECTORY_FIELDS =
  'fullName firstName middleName lastName email kind isDisabled';

const toDirectoryUser = (user: {
  _id: { toString(): string };
  fullName?: string;
  firstName?: string;
  middleName?: string;
  lastName?: string;
  email?: string;
  kind?: UserKind;
  isDisabled?: boolean;
}): DirectoryUser => ({
  userId: user._id.toString(),
  displayName: displayNameOf(user),
  ...(user.email ? { email: user.email } : {}),
  ...(user.firstName ? { firstName: user.firstName } : {}),
  ...(user.middleName ? { middleName: user.middleName } : {}),
  ...(user.lastName ? { lastName: user.lastName } : {}),
  ...(user.fullName ? { fullName: user.fullName } : {}),
  kind: user.kind ?? 'human',
  isDisabled: user.isDisabled === true,
});

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
      .select(DIRECTORY_FIELDS)
      .lean()
      .exec();

    return users.map(toDirectoryUser);
  }

  async searchOrgMembers(
    orgId: string,
    q: string,
    limit: number,
  ): Promise<readonly DirectoryUser[]> {
    const tokens = searchTokens(q);
    if (tokens.length === 0 || limit <= 0 || !mongoose.Types.ObjectId.isValid(orgId)) {
      return [];
    }
    const users = await Users.find({
      orgId: new mongoose.Types.ObjectId(orgId),
      isDeleted: false,
      isDisabled: { $ne: true },
      kind: { $in: ['human', null] },
      $and: searchFilter(tokens),
    })
      .select(DIRECTORY_FIELDS)
      .sort({ fullName: 1, _id: 1 })
      .limit(limit)
      .lean()
      .exec();
    return users.map(toDirectoryUser);
  }
}
