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

  // Global replace, matching Python's str.replace which strips every space.
  const eventType = connector.replace(/ /g, '').toLowerCase() + '.resync';

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
