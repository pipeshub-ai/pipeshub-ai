import { Types } from 'mongoose';
import { Org } from '../schema/org.schema';

export interface IOrgDirectory {
  /** Never empty: falls back to a generic label when the org has no name. */
  displayName(orgId: string): Promise<string>;
}

const FALLBACK_NAME = 'your organization';

const firstNonEmpty = (
  ...names: Array<string | undefined>
): string | undefined =>
  names.map((n) => n?.trim()).find((n) => n !== undefined && n !== '');

export class MongoOrgDirectory implements IOrgDirectory {
  async displayName(orgId: string): Promise<string> {
    if (!Types.ObjectId.isValid(orgId)) {
      return FALLBACK_NAME;
    }
    const org = await Org.findById(orgId)
      .select('shortName registeredName')
      .lean<{ shortName?: string; registeredName?: string }>()
      .exec();
    return firstNonEmpty(org?.shortName, org?.registeredName) ?? FALLBACK_NAME;
  }
}
