import { Types } from "mongoose";

// Base interface for common schedule properties
export interface IBaseCrawlingSchedule {
    isEnabled: boolean;
    nextRunTime?: Date;
    lastRunTime?: Date;
    createdBy: Types.ObjectId;
    lastUpdatedBy: Types.ObjectId;
    createdAt?: Date;
    updatedAt?: Date;
  }