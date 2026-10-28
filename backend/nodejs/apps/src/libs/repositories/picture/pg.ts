/** PostgreSQL implementation of the picture repository (`DOC_STORE=postgres`): one row per person, the photo kept as the base64 text callers pass. */
import { stripNul } from '../nul';
import { assertOrg, inUnit, newObjectId, oidOf, oidsOf } from '../pg-util';
import type { PictureDto, PictureRepository } from './types';

interface PictureRow {
  id: string;
  user_id: string;
  pic: string | null;
  pic_url: string | null;
  mime_type: string | null;
  version: number;
}

const COLS = 'p.id, p.user_id, p.pic, p.pic_url, p.mime_type, p.version';

const dto = (r: PictureRow, orgId: string): PictureDto => ({
  _id: r.id,
  userId: r.user_id,
  orgId,
  pic: r.pic,
  ...(r.pic_url !== null && { picUrl: r.pic_url }),
  mimeType: r.mime_type,
  __v: r.version,
});

export class PgPictureRepository implements PictureRepository {
  async get(orgId: string, userId: string): Promise<PictureDto | null> {
    const uid = oidOf(userId);
    if (uid === undefined) return null;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<PictureRow>(
        `/* pipeshub:identity.picture_get */ SELECT ${COLS} FROM identity.user_pictures p WHERE p.org_id = $1 AND p.user_id = $2::text`,
        [k.orgId, uid],
      );
      return rows[0] ? dto(rows[0], k.orgId) : null;
    });
  }

  async set(
    orgId: string,
    userId: string,
    pic: string,
    mimeType: string,
  ): Promise<void> {
    const uid = oidOf(userId);
    if (uid === undefined) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        `/* pipeshub:identity.picture_set */ INSERT INTO identity.user_pictures (org_id, user_id, id, pic, mime_type) VALUES ($1, $2, $3, $4, $5)
          ON CONFLICT (org_id, user_id) DO UPDATE SET pic = excluded.pic, mime_type = excluded.mime_type`,
        [
          k.orgId,
          uid,
          newObjectId(),
          stripNul(pic, 'picture', 'pic'),
          mimeType,
        ],
      );
    });
  }

  async remove(orgId: string, userId: string): Promise<PictureDto | null> {
    const uid = oidOf(userId);
    if (uid === undefined) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<PictureRow>(
        `/* pipeshub:identity.picture_remove */ UPDATE identity.user_pictures p SET pic = NULL, mime_type = NULL WHERE p.org_id = $1 AND p.user_id = $2::text RETURNING ${COLS}`,
        [k.orgId, uid],
      );
      return rows[0] ? dto(rows[0], k.orgId) : null;
    });
  }

  async listSet(orgId: string, userIds: string[]): Promise<PictureDto[]> {
    const ids = oidsOf(userIds);
    if (ids.length === 0) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<PictureRow>(
        `/* pipeshub:identity.picture_list_set */ SELECT ${COLS} FROM identity.user_pictures p WHERE p.org_id = $1 AND p.user_id = ANY($2::text[]) AND p.pic IS NOT NULL ORDER BY p.id`,
        [k.orgId, ids],
      );
      return rows.map((r) => dto(r, k.orgId));
    });
  }
}
