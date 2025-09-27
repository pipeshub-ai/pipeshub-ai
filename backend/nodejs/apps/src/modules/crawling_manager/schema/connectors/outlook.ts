import { IBaseConnectorConfig } from './base_connector';
import { ConnectorType } from '../enums';

export interface IOutlookSettings {
  excludedFolders?: string[];
  dateRange?: {
    startDate?: string;
    endDate?: string;
  };
  maxEmailsPerSync?: number;
}

export interface IOutlookConnectorConfig extends IBaseConnectorConfig {
  connectorType: ConnectorType.OUTLOOK;
  settings: IOutlookSettings;
}