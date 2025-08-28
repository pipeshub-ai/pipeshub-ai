import { ConnectorConfigLayout } from './components/connector-config-layout';
import ConfigureConnectorDialog from './components/configure-connector-company-dialog';

const AtlassianConnector = () => (
  <ConnectorConfigLayout
    connectorId="atlassian"
    accountType="business"
    configDialogComponent={ConfigureConnectorDialog}
    statsConnectorNames={['Atlassian']}
  />
);

export default AtlassianConnector;