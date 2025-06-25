import { IBaseConnectorConfig } from './base_connector';
import { ConnectorType } from '../enums';

export interface IOneDriveSharePointSettings {
  excludedSites?: string[];
  excludedLibraries?: string[];
  includePersonalOneDrive?: boolean;
  includeSharePointSites?: boolean;
}

  export interface IOneDriveConnectorConfig extends IBaseConnectorConfig {
    connectorType: ConnectorType.ONE_DRIVE;
    settings: IOneDriveSharePointSettings;
  }