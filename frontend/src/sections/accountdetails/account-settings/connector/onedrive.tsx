import { ConnectorConfigLayout } from './components/connector-config-layout';
import ConfigureConnectorDialog from './components/configure-connector-company-dialog';

const OneDriveConnector = () => (
  <ConnectorConfigLayout
    connectorId="onedrive"
    accountType="business"
    configDialogComponent={ConfigureConnectorDialog}
    statsConnectorNames={['OneDrive']}
  />
);

export default OneDriveConnector;