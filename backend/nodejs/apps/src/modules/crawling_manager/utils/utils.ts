import {
  EventType, Event,
  ReindexAllRecordEvent,
} from '../../knowledge_base/services/sync_events.service';

export const constructSyncConnectorEvent = (
  orgId: string,
  connector: string,
) : Event => {
  let eventType: EventType;
  if (connector === 'drive') {
    eventType = EventType.SyncDriveEvent;
  } else if (connector === 'gmail') {
    eventType = EventType.SyncGmailEvent;
  } else if (connector === 'onedrive') {
    eventType = EventType.SyncOneDriveEvent;
  } else {
    // Default to ReindexAllRecordEvent for unsupported connectors like 'slack'
    eventType = EventType.ReindexAllRecordEvent;
  }

  const payload = {
    orgId: orgId,
    origin: 'CONNECTOR',
    connector: connector,
    createdAtTimestamp: Date.now().toString(),
    updatedAtTimestamp: Date.now().toString(),
    sourceCreatedAtTimestamp: Date.now().toString(),
  } as ReindexAllRecordEvent;

  const event : Event = {
    eventType: eventType,
    timestamp: Date.now(),
    payload: payload,
  };

  return event;
};
