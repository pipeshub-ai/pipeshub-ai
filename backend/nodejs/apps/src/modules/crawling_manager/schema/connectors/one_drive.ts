import { Schema } from 'mongoose';

export interface IOneDriveSharePointSettings {
  excludedSites?: string[];
  excludedLibraries?: string[];
  includePersonalOneDrive?: boolean;
  includeSharePointSites?: boolean;
}

// OneDrive/SharePoint Settings Schema
// @ts-ignore - Temporarily ignoring unused declaration
export const OneDriveSharePointSettingsSchema =
  new Schema<IOneDriveSharePointSettings>(
    {
      excludedSites: [{ type: String }],
      excludedLibraries: [{ type: String }],
      includePersonalOneDrive: { type: Boolean, default: true },
      includeSharePointSites: { type: Boolean, default: true },
    },
    { _id: false },
  );
