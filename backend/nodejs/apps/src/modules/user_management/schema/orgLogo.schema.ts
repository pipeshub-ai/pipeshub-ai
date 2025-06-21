import mongoose, { model,Schema,Types,Document } from "mongoose";

const ObjectId = mongoose.Schema.Types.ObjectId;

interface IorgLogo extends Document{
    orgId?: Types.ObjectId;
    logo?:string|null;
    logoUrl?:string|null;
    mimeType?:string|null;
}

const orgLogoSchema = new Schema<IorgLogo>({
  orgId: {
    type: ObjectId,
    ref: "orgs",
  },
  logo: {
    type: String,
     // Store the logo as a base64 string
  },
  logoUrl: {
    type: String,
  },
  mimeType: {
    type: String, // Store the MIME type (e.g., 'image/png', 'image/jpeg')
  },
});

export const OrgLogos = model<IorgLogo>("org-logos", orgLogoSchema, "org-logos");