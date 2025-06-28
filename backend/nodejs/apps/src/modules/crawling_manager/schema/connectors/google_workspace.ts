import { Schema } from 'mongoose';

export interface IGoogleWorkspaceSettings {
  excludedDrives?: string[];
  excludedFolders?: string[];
  includeSharedDrives?: boolean;
  includeMyDrive?: boolean;
}

// @ts-ignore - Temporarily ignoring unused declaration
export const GoogleDriveSettingsSchema = new Schema<IGoogleWorkspaceSettings>(
  {
    excludedDrives: [{ type: String }],
    excludedFolders: [{ type: String }],
    includeSharedDrives: { type: Boolean, default: true },
    includeMyDrive: { type: Boolean, default: true },
  },
  { _id: false },
);
