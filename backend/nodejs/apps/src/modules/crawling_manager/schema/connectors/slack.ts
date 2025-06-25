import { IBaseConnectorConfig } from './base_connector';
import { ConnectorType } from '../enums';

export interface ISlackSettings {
  excludedChannels?: string[];
  excludedWorkspaces?: string[];
  includePrivateChannels?: boolean;
  includeDMs?: boolean;
  includeThreads?: boolean;
}

export interface ISlackConnectorConfig extends IBaseConnectorConfig {
  connectorType: ConnectorType.SLACK;
  settings: ISlackSettings;
}
