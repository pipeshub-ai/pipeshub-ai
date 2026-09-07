import {
  Event,
  ConnectorSyncEvent,
} from '../../knowledge_base/services/sync_events.service';

export const constructSyncConnectorEvent = (
  orgId: string,
  connector: string,
  connectorId: string,
  userId?: string,
) : Event => {

  const eventType = connector.replace(' ', '').toLowerCase() + '.resync';

  const payload: ConnectorSyncEvent = {
    orgId: orgId,
    origin: 'CONNECTOR',
    connector: connector,
    connectorId: connectorId,
    syncedBy: userId,
    createdAtTimestamp: Date.now().toString(),
    updatedAtTimestamp: Date.now().toString(),
    sourceCreatedAtTimestamp: Date.now().toString(),
  };

  const event : Event = {
    eventType: eventType ,
    timestamp: Date.now(),
    payload: payload,
  };

  return event;
};
