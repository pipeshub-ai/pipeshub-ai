/** Picture repository: a person's profile photo, stored as base64 with its MIME type. */

export interface PictureDto {
  _id: string;
  userId?: string;
  orgId?: string;
  pic?: string | null;
  picUrl?: string | null;
  mimeType?: string | null;
  __v?: number;
}

export interface PictureRepository {
  get(orgId: string, userId: string): Promise<PictureDto | null>;
  /** Stores or replaces the photo. */
  set(
    orgId: string,
    userId: string,
    pic: string,
    mimeType: string,
  ): Promise<void>;
  /** Empties the photo but keeps the row; null when the person never had one. */
  remove(orgId: string, userId: string): Promise<PictureDto | null>;
  /** Photos that are set, for these people. */
  listSet(orgId: string, userIds: string[]): Promise<PictureDto[]>;
}
