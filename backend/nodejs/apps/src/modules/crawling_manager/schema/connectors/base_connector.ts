import { Types } from 'mongoose';

// Base interface for common connector properties
export interface IBaseConnectorConfig {
    isEnabled: boolean;
    lastUpdatedBy: Types.ObjectId;
    updatedAt?: Date;
}