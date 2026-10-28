import { UserDisplayPicture } from '../../../modules/user_management/schema/userDp.schema';
import { toPlain } from '../plain';
import type { PictureDto, PictureRepository } from './types';

const dto = (row: unknown): PictureDto => toPlain<PictureDto>(row);

export class MongoPictureRepository implements PictureRepository {
  async get(orgId: string, userId: string): Promise<PictureDto | null> {
    const row = await UserDisplayPicture.findOne({ orgId, userId })
      .lean()
      .exec();
    return row ? dto(row) : null;
  }

  async set(
    orgId: string,
    userId: string,
    pic: string,
    mimeType: string,
  ): Promise<void> {
    await UserDisplayPicture.findOneAndUpdate(
      { orgId, userId },
      { orgId, userId, pic, mimeType },
      { returnDocument: 'after', upsert: true },
    );
  }

  async remove(orgId: string, userId: string): Promise<PictureDto | null> {
    const row = await UserDisplayPicture.findOne({ orgId, userId }).exec();
    if (!row) return null;
    row.pic = null;
    row.mimeType = null;
    await row.save();
    return dto(row);
  }

  async listSet(orgId: string, userIds: string[]): Promise<PictureDto[]> {
    const rows = await UserDisplayPicture.find({
      orgId,
      userId: { $in: userIds },
      pic: { $ne: null },
    })
      .lean()
      .exec();
    return rows.map(dto);
  }
}
