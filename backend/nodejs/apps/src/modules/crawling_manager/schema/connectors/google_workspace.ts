import { IBaseConnectorConfig } from './base_connector';
import { ConnectorType } from '../enums';

export interface IGoogleWorkspaceSettings {
  excludedDrives?: string[];
  excludedFolders?: string[];
  includeSharedDrives?: boolean;
  includeMyDrive?: boolean;
}

export interface IGoogleWorkspaceConnectorConfig extends IBaseConnectorConfig {
  connectorType: ConnectorType.GOOGLE_WORKSPACE;
  settings: IGoogleWorkspaceSettings;
}