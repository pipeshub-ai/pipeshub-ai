import { Schema, model } from 'mongoose';

export interface SessionFamilyDocument {
  orgId: string;
  familyId: string;
  userId: string;
  currentTokenId: string;
  previousTokenId: string | null;
  rotatedAt: Date | null;
  expiresAt: Date;
  revokedAt: Date | null;
  revokedReason: string | null;
}

const sessionFamilySchema = new Schema<SessionFamilyDocument>(
  {
    orgId: { type: String, required: true },
    familyId: { type: String, required: true },
    userId: { type: String, required: true },
    currentTokenId: { type: String, required: true },
    previousTokenId: { type: String, default: null },
    rotatedAt: { type: Date, default: null },
    expiresAt: { type: Date, required: true },
    revokedAt: { type: Date, default: null },
    revokedReason: { type: String, default: null },
  },
  { timestamps: true },
);

sessionFamilySchema.index(
  { orgId: 1, familyId: 1 },
  { unique: true, name: 'org_family' },
);
// Kept an hour past the end of the sign-in, so a late refresh is told "expired" by the token's own check.
sessionFamilySchema.index(
  { expiresAt: 1 },
  { expireAfterSeconds: 3600, name: 'expiry' },
);

export const SessionFamilyModel = model<SessionFamilyDocument>(
  'SessionFamily',
  sessionFamilySchema,
  'session_families',
);
