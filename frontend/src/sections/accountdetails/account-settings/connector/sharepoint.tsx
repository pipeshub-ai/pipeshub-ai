import { ConnectorConfigLayout } from './components/connector-config-layout';
import ConfigureConnectorDialog from './components/configure-connector-company-dialog';

const SharePointConnector = () => (
  <ConnectorConfigLayout
    connectorId="sharepoint"
    accountType="business"
    configDialogComponent={ConfigureConnectorDialog}
    statsConnectorNames={['SharePoint']}
  />
);

export default SharePointConnector;