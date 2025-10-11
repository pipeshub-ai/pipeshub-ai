import {
  Event,
  ReindexAllRecordEvent,
} from '../../knowledge_base/services/sync_events.service';

export const constructSyncConnectorEvent = (
  orgId: string,
  connector: string,
) : Event => {

  const eventType = connector.replace(' ', '').toLowerCase() + '.resync';

  const payload = {
    orgId: orgId,
    origin: 'CONNECTOR',
    connector: connector,
    createdAtTimestamp: Date.now().toString(),
    updatedAtTimestamp: Date.now().toString(),
    sourceCreatedAtTimestamp: Date.now().toString(),
  } as ReindexAllRecordEvent;

  const event : Event = {
    eventType: eventType ,
    timestamp: Date.now(),
    payload: payload,
  };

  return event;
};
