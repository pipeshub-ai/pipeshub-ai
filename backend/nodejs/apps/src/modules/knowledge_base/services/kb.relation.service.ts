import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { IRecordDocument } from '../types/record';
import { IFileRecordDocument } from '../types/file_record';
import {
  InternalServerError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import {
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
import {
  DeletedRecordEvent,
  NewRecordEvent,
  UpdateRecordEvent,
} from './records_events.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { endpoint } from '../../storage/constants/constants';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import {
  SyncEventProducer,
  Event as SyncEvent,
  BaseSyncEvent,
} from './sync_events.service';
import {
  isLocalFsConnector,
  LOCAL_FS_CONNECTOR_KEY,
} from '../../../utils/local-fs-utils';
import {
  IServiceFileRecord,
  IServiceRecord
} from '../types/service.records.response';


const logger = Logger.getInstance({
  service: 'Knowledge Base Service',
});

export const RESYNC_NOT_QUEUED_MESSAGE =
  "We couldn't start this sync because PipesHub couldn't queue it. Nothing was synced. Try again in a minute; if it keeps happening, ask your admin to check the services page.";

@injectable()
export class RecordRelationService {

  constructor(
    @inject(SyncEventProducer) readonly syncEventProducer: SyncEventProducer,
    private readonly defaultConfig: DefaultStorageConfig,
  ) {
    this.initializeSyncEventProducer();
  }

  private async initializeSyncEventProducer() {
    try {
      await this.syncEventProducer.start();
      logger.info('Sync Event producer initialized successfully');
    } catch (error) {
      logger.error('Failed to initialize Sync event producer', error);
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('start the knowledge base')),
      );
    }
  }

  /**
   * Creates a standardized update record event payload
   * @param record The updated record
   * @returns UpdateRecordEvent payload for Kafka
   */
  async createUpdateRecordEventPayload(
    record: IRecordDocument,
    fileRecord: IFileRecordDocument,
    keyValueStoreService: KeyValueStoreService,
  ): Promise<UpdateRecordEvent> {
    // Generate signed URL route based on record information
    const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';

    const storageUrl =
      JSON.parse(url).storage.endpoint || this.defaultConfig.endpoint;
    const signedUrlRoute = `${storageUrl}/api/v1/document/internal/${record.externalRecordId}/download`;
    let extension = '';
    if (fileRecord && fileRecord.extension) {
      extension = fileRecord.extension;
    }
    let mimeType = '';
    if (fileRecord && fileRecord.mimeType) {
      mimeType = fileRecord.mimeType;
    }
    return {
      orgId: record.orgId,
      connectorId: record.connectorId,
      recordId: record._key,
      version: record.version || 1,
      signedUrlRoute: signedUrlRoute,
      extension: extension,
      mimeType: mimeType,
      summaryDocumentId: record.summaryDocumentId,
      updatedAtTimestamp: (record.updatedAtTimestamp || Date.now()).toString(),
      sourceLastModifiedTimestamp: (
        record.sourceLastModifiedTimestamp ||
        record.updatedAtTimestamp ||
        Date.now()
      ).toString(),
      virtualRecordId: record.virtualRecordId,
    };
  }

  /**
   * Creates a standardized delete record event payload
   * @param record The record being deleted
   * @param userId The user performing the deletion
   * @returns DeletedRecordEvent payload for Kafka
   */
  createDeletedRecordEventPayload(
    record: IRecordDocument | IServiceRecord,
    fileRecord: IFileRecordDocument | IServiceFileRecord,
  ): DeletedRecordEvent {
    let extension = '';
    if (fileRecord && fileRecord.extension) {
      extension = fileRecord.extension;
    }
    let mimeType = '';
    if (fileRecord && fileRecord.mimeType) {
      mimeType = fileRecord.mimeType;
    }
    return {
      orgId: record.orgId,
      connectorId: record.connectorId,
      recordId: record._key,
      version: record.version || 1,
      extension: extension,
      mimeType: mimeType,
      summaryDocumentId: record.summaryDocumentId,
      virtualRecordId: record.virtualRecordId,
    };
  }



  // New method for creating reindex event payload
  async createReindexRecordEventPayload(
    record: any,
    fileRecord: IFileRecordDocument,
    keyValueStoreService: KeyValueStoreService,
  ): Promise<NewRecordEvent> {
    // Generate signed URL route based on record information
    const url = (await keyValueStoreService.get<string>('endpoint')) || '{}';

    const storageUrl =
      JSON.parse(url).storage?.endpoint || this.defaultConfig.endpoint;
    const signedUrlRoute = `${storageUrl}/api/v1/document/internal/${record.externalRecordId}/download`;
    let mimeType = '';
    if (fileRecord && fileRecord.mimeType) {
      mimeType = fileRecord.mimeType;
    }
    return {
      orgId: record.orgId,
      connectorId: record.connectorId,
      recordId: record._key,
      version: record.version || 1,
      signedUrlRoute: signedUrlRoute,
      recordName: record.recordName,
      recordType: record.recordType,
      origin: record.origin,
      extension: record.fileRecord.extension,
      mimeType: mimeType,
      createdAtTimestamp: Date.now().toString(),
      updatedAtTimestamp: Date.now().toString(),
      sourceCreatedAtTimestamp: Date.now().toString(),
    };
  }

  async resyncConnectorRecords(resyncConnectorPayload: any): Promise<any> {
    try {
      const resyncPayload =
        await this.createResyncConnectorEventPayload(resyncConnectorPayload);
      // Global replace, matching Python's str.replace, which is already global.
      // The single-space version happened to route correctly because this value
      // is normalized twice (normalizeAppName, then here) and the consumer
      // normalizes again -- but it left an embedded space in the published
      // payload.connector for three-word types. Same result, one pass.
      const eventType =
        resyncPayload.connector.replace(/ /g, '').toLowerCase() + '.resync';
      const event: SyncEvent = {
        eventType: eventType,
        timestamp: Date.now(),
        payload: resyncPayload,
      };

      await this.syncEventProducer.publishEvent(event);
      logger.info(
        `Published resync connector event for app ${resyncConnectorPayload.connectorName}`,
      );

      return { success: true };
    } catch (eventError: any) {
      logger.error('Failed to publish resync connector event', {
        error: eventError,
      });
      if (eventError?.statusCode === 409) {
        throw eventError;
      }
      throw markClientSafe(new ServiceUnavailableError(RESYNC_NOT_QUEUED_MESSAGE));
    }
  }

  async createResyncConnectorEventPayload(
    resyncConnectorEventPayload: any,
  ): Promise<BaseSyncEvent> {
    const connectorName = isLocalFsConnector(
      resyncConnectorEventPayload.connectorName,
    )
      ? LOCAL_FS_CONNECTOR_KEY
      : resyncConnectorEventPayload.connectorName;

    return {
      orgId: resyncConnectorEventPayload.orgId,
      origin: resyncConnectorEventPayload.origin,
      connector: connectorName,
      connectorId: resyncConnectorEventPayload.connectorId,
      syncedBy: resyncConnectorEventPayload.userId,
      fullSync: resyncConnectorEventPayload.fullSync,
      createdAtTimestamp: Date.now().toString(),
      updatedAtTimestamp: Date.now().toString(),
      sourceCreatedAtTimestamp: Date.now().toString(),
    };
  }

}
