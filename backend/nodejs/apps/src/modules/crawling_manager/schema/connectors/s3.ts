import { ConnectorType } from "../enums";
import { IBaseConnectorConfig } from "./base_connector";

export interface IS3Settings {
    bucketName: string;
    region?: string;
    prefix?: string;
    excludedPrefixes?: string[];
    includeMetadata?: boolean;
    maxFileSize?: number;
  }
  
  export interface IS3ConnectorConfig extends IBaseConnectorConfig {
    connectorType: ConnectorType.S3;
    settings: IS3Settings;
  }