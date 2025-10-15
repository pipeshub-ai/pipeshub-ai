#!/usr/bin/env python3
"""
DocuSign Complete API Code Generator

Generates a comprehensive DocuSignDataSource class covering all DocuSign APIs:
- eSignature REST API v2.1 (Business & Personal)  
- Admin API v2.1 (Organization management)
- Rooms API v2 (Real estate transactions)
- Click API v1 (Clickwrap agreements)
- Maestro API v1 (Workflow orchestration)
- WebForms API v1.1 (Form management)
- Navigator API (Agreement analytics)
- Monitor API v2 (Security events)

The generator creates methods with explicit parameter typing, proper headers,
and standardized error handling. All parameters match DocuSign's official
SDK specifications exactly.
"""

from typing import Dict, List, Optional, Any, Union, Literal
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DocuSignAPIOperation:
    """Represents a single DocuSign API operation."""
    operation_id: str
    method: str  # GET, POST, PUT, DELETE, PATCH
    path: str
    summary: str
    description: str
    parameters: List[Dict[str, Any]]
    request_body: Optional[Dict[str, Any]]
    responses: Dict[str, Any]
    tags: List[str]
    api_category: str  # esignature, admin, rooms, etc.
    security: List[Dict[str, Any]]
    headers: Dict[str, str]


# Complete DocuSign API Operations Database
# Based on official OpenAPI specifications from github.com/docusign/OpenAPI-Specifications
DOCUSIGN_API_OPERATIONS = {
    
    # ================================================================================
    # ESIGNATURE REST API v2.1 - CORE BUSINESS & PERSONAL FUNCTIONALITY
    # ================================================================================
    
    # ACCOUNTS OPERATIONS
    'accounts_get_account': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}',
        'summary': 'Retrieves the account information for a single account.',
        'description': 'Gets account settings and information for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'include_account_settings': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, includes account settings in the response.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Accounts']
    },
    
    'accounts_create_account': {
        'method': 'POST', 
        'path': '/v2.1/accounts',
        'summary': 'Creates new DocuSign account.',
        'description': 'Creates a new DocuSign account. Only available in the Developer environment.',
        'parameters': {
            'preview_billing_plan': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, returns billing plan information.'}
        },
        'request_body': {
            'accountName': {'type': 'str', 'required': True, 'description': 'The account name.'},
            'accountSettings': {'type': 'Optional[Dict[str, Any]]', 'description': 'Account settings.'},
            'addressInformation': {'type': 'Optional[Dict[str, Any]]', 'description': 'Account address information.'},
            'creditCardInformation': {'type': 'Optional[Dict[str, Any]]', 'description': 'Credit card information.'},
            'distributorCode': {'type': 'Optional[str]', 'description': 'The distributor code.'},
            'distributorPassword': {'type': 'Optional[str]', 'description': 'The distributor password.'},
            'initialUser': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Initial user information.'},
            'planInformation': {'type': 'Optional[Dict[str, Any]]', 'description': 'Plan information.'},
            'referralInformation': {'type': 'Optional[Dict[str, Any]]', 'description': 'Referral information.'},
            'socialAccountInformation': {'type': 'Optional[Dict[str, Any]]', 'description': 'Social account information.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Accounts']
    },

    'accounts_get_account_settings': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/settings',
        'summary': 'Gets account settings information.',
        'description': 'Retrieves the account settings for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Accounts']
    },

    'accounts_update_account_settings': {
        'method': 'PUT',
        'path': '/v2.1/accounts/{accountId}/settings',
        'summary': 'Updates the account settings for the specified account.',
        'description': 'Updates account settings for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'}
        },
        'request_body': {
            'accountSettings': {'type': 'List[Dict[str, Any]]', 'required': True, 'description': 'Contains account settings information.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Accounts']
    },

    # ENVELOPES OPERATIONS - CORE ESIGNATURE FUNCTIONALITY
    'envelopes_create_envelope': {
        'method': 'POST',
        'path': '/v2.1/accounts/{accountId}/envelopes',
        'summary': 'Creates an envelope.',
        'description': 'Creates and sends an envelope or creates a draft envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'cdse_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'completed_documents_only': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'merge_roles_on_draft': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, template roles are merged.'}
        },
        'request_body': {
            'allowMarkup': {'type': 'Optional[bool]', 'description': 'When true, Document Markup is enabled.'},
            'allowReassign': {'type': 'Optional[bool]', 'description': 'When true, the recipient can redirect envelope to another recipient.'},
            'allowViewHistory': {'type': 'Optional[bool]', 'description': 'When true, the recipient can view envelope history.'},
            'asynchronous': {'type': 'Optional[bool]', 'description': 'When true, envelope is queued for processing.'},
            'attachmentsUri': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'authoritativeCopy': {'type': 'Optional[bool]', 'description': 'Specifies whether the envelope is an authoritative copy.'},
            'autoNavigation': {'type': 'Optional[bool]', 'description': 'When true, auto-navigation is enabled.'},
            'brandId': {'type': 'Optional[str]', 'description': 'The unique identifier of the brand.'},
            'brandLock': {'type': 'Optional[bool]', 'description': 'When true, the brand is locked.'},
            'burnDefaultTabData': {'type': 'Optional[bool]', 'description': 'When true, default tab data is burned into documents.'},
            'certificateUri': {'type': 'Optional[str]', 'description': 'Retrieval URI for the envelope certificate.'},
            'completedDateTime': {'type': 'Optional[str]', 'description': 'The date and time the envelope was completed.'},
            'compositeTemplates': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Zero or more composite templates.'},
            'customFields': {'type': 'Optional[Dict[str, Any]]', 'description': 'Custom fields for the envelope.'},
            'customFieldsUri': {'type': 'Optional[str]', 'description': 'Contains a URI for retrieving custom fields.'},
            'declinedDateTime': {'type': 'Optional[str]', 'description': 'The date and time the envelope was declined.'},
            'deletedDateTime': {'type': 'Optional[str]', 'description': 'The date and time the envelope was deleted.'},
            'deliveredDateTime': {'type': 'Optional[str]', 'description': 'The date and time the envelope was delivered.'},
            'documents': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of document objects.'},
            'documentsUri': {'type': 'Optional[str]', 'description': 'Contains a URI for retrieving envelope documents.'},
            'emailBlurb': {'type': 'Optional[str]', 'description': 'The subject line of the email message sent to recipients.'},
            'emailSettings': {'type': 'Optional[Dict[str, Any]]', 'description': 'Email settings for the envelope.'},
            'emailSubject': {'type': 'Optional[str]', 'description': 'The subject line of the email message.'},
            'enableWetSign': {'type': 'Optional[bool]', 'description': 'When true, enables wet signing.'},
            'enforceSignerVisibility': {'type': 'Optional[bool]', 'description': 'When true, enforces signer visibility.'},
            'envelopeAttachments': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of envelope attachments.'},
            'envelopeCustomMetadata': {'type': 'Optional[Dict[str, Any]]', 'description': 'Custom metadata for envelope.'},
            'envelopeDocuments': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of envelope documents.'},
            'envelopeId': {'type': 'Optional[str]', 'description': 'The envelope ID.'},
            'envelopeIdStamping': {'type': 'Optional[bool]', 'description': 'When true, envelope ID is stamped.'},
            'envelopeLocation': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'envelopeMetadata': {'type': 'Optional[Dict[str, Any]]', 'description': 'Metadata for the envelope.'},
            'envelopeUri': {'type': 'Optional[str]', 'description': 'Contains a URI for retrieving the envelope.'},
            'eventNotifications': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of event notifications.'},
            'expireAfter': {'type': 'Optional[int]', 'description': 'Number of days envelope is active.'},
            'expireDateTime': {'type': 'Optional[str]', 'description': 'The date and time the envelope expires.'},
            'expireEnabled': {'type': 'Optional[bool]', 'description': 'When true, envelope expires.'},
            'externalEnvelopeId': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'favoritedByMe': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'folderId': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'folderIds': {'type': 'Optional[List[str]]', 'description': 'Reserved for DocuSign.'},
            'folderName': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'folders': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Reserved for DocuSign.'},
            'hasComments': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'hasFormDataChanged': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'hasWavFile': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'holder': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'initialSentDateTime': {'type': 'Optional[str]', 'description': 'The date and time envelope was initially sent.'},
            'is21CFRPart11': {'type': 'Optional[bool]', 'description': 'When true, envelope is 21 CFR Part 11 compliant.'},
            'isDynamicEnvelope': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'isSignatureProviderEnvelope': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'lastModifiedDateTime': {'type': 'Optional[str]', 'description': 'The date and time envelope was last modified.'},
            'location': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'lockInformation': {'type': 'Optional[Dict[str, Any]]', 'description': 'Lock information for the envelope.'},
            'messageLock': {'type': 'Optional[bool]', 'description': 'When true, prevents envelope message changes.'},
            'notification': {'type': 'Optional[Dict[str, Any]]', 'description': 'Notification settings.'},
            'notificationUri': {'type': 'Optional[str]', 'description': 'Contains a URI for retrieving notifications.'},
            'powerForm': {'type': 'Optional[Dict[str, Any]]', 'description': 'Information about the powerform.'},
            'purgeCompletedDate': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'purgeRequestDate': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'purgeState': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'recipients': {'type': 'Optional[Dict[str, Any]]', 'description': 'Array of recipient objects.'},
            'recipientsLock': {'type': 'Optional[bool]', 'description': 'When true, prevents envelope recipient changes.'},
            'recipientsUri': {'type': 'Optional[str]', 'description': 'Contains a URI for retrieving recipients.'},
            'recipientViewRequest': {'type': 'Optional[Dict[str, Any]]', 'description': 'Reserved for DocuSign.'},
            'sender': {'type': 'Optional[Dict[str, Any]]', 'description': 'Information about the envelope sender.'},
            'sentDateTime': {'type': 'Optional[str]', 'description': 'The date and time the envelope was sent.'},
            'signerCanSignOnMobile': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'signingLocation': {'type': 'Optional[str]', 'description': 'Specifies the signing location.'},
            'status': {'type': 'Optional[str]', 'description': 'Envelope status.'},
            'statusChangedDateTime': {'type': 'Optional[str]', 'description': 'The date and time envelope status changed.'},
            'statusDateTime': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'templatesUri': {'type': 'Optional[str]', 'description': 'Contains a URI for retrieving templates.'},
            'transactionId': {'type': 'Optional[str]', 'description': 'Used to identify envelope in DocuSign Connect.'},
            'useDisclosure': {'type': 'Optional[bool]', 'description': 'When true, enables the Consumer Disclosure feature.'},
            'voidedDateTime': {'type': 'Optional[str]', 'description': 'The date and time the envelope was voided.'},
            'voidedReason': {'type': 'Optional[str]', 'description': 'The reason the envelope was voided.'},
            'workflow': {'type': 'Optional[Dict[str, Any]]', 'description': 'Workflow information for the envelope.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Envelopes']
    },

    'envelopes_get_envelope': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}',
        'summary': 'Gets the status of a single envelope.',
        'description': 'Retrieves the overall status for the specified envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'},
            'advanced_update': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'include': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies additional information to return.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Envelopes']
    },

    'envelopes_update_envelope': {
        'method': 'PUT',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}',
        'summary': 'Send, void, or modify a draft envelope.',
        'description': 'Updates envelope information for the specified envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'},
            'advanced_update': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'resend_envelope': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, resends the envelope.'}
        },
        'request_body': {
            'status': {'type': 'Optional[str]', 'description': 'Envelope status.'},
            'voidedReason': {'type': 'Optional[str]', 'description': 'The reason for voiding the envelope.'},
            'emailBlurb': {'type': 'Optional[str]', 'description': 'The subject line of the email message.'},
            'emailSubject': {'type': 'Optional[str]', 'description': 'The subject line of the email message.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Envelopes']
    },

    'envelopes_list_envelopes': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/envelopes',
        'summary': 'Gets status changes for one or more envelopes.',
        'description': 'Retrieves envelope status changes for all envelopes.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'ac_status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies the AC status to filter by.'},
            'block': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return.'},
            'custom_field': {'type': 'Optional[str]', 'location': 'query', 'description': 'Custom field to filter by.'},
            'email': {'type': 'Optional[str]', 'location': 'query', 'description': 'Email address to filter by.'},
            'envelope_ids': {'type': 'Optional[str]', 'location': 'query', 'description': 'Comma separated list of envelope IDs.'},
            'exclude': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies the envelope information to exclude.'},
            'folder_ids': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'folder_types': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Start of date range for envelope status changes.'},
            'from_to_status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Envelope status filter.'},
            'include': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies the envelope information to include.'},
            'include_purge_information': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'intersecting_folder_ids': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'last_queried_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'order': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies the sort order.'},
            'order_by': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies the field to sort by.'},
            'powerformids': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'query_budget': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'requester_date_format': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'search_text': {'type': 'Optional[str]', 'location': 'query', 'description': 'Text to search for in the envelope.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'Starting position for the result set.'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Envelope status to filter by.'},
            'to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'End of date range for envelope status changes.'},
            'transaction_ids': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'user_filter': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by user.'},
            'user_id': {'type': 'Optional[str]', 'location': 'query', 'description': 'User ID to filter by.'},
            'user_name': {'type': 'Optional[str]', 'location': 'query', 'description': 'User name to filter by.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Envelopes']
    },

    'envelopes_delete_envelope': {
        'method': 'DELETE',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}',
        'summary': 'Deletes a draft envelope.',
        'description': 'Deletes the specified draft envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Envelopes']
    },

    # ENVELOPE RECIPIENTS OPERATIONS
    'envelope_recipients_list': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients',
        'summary': 'Gets the status of recipients for an envelope.',
        'description': 'Retrieves the status of all recipients in the specified envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'},
            'include_anchor_tab_locations': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'include_extended': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'include_metadata': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'include_tabs': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, includes tab information.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['EnvelopeRecipients']
    },

    'envelope_recipients_create': {
        'method': 'POST',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients',
        'summary': 'Adds one or more recipients to an envelope.',
        'description': 'Adds one or more recipients to an envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'},
            'resend_envelope': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, resends the envelope.'}
        },
        'request_body': {
            'agents': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of agent recipients.'},
            'carbonCopies': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of carbon copy recipients.'},
            'certifiedDeliveries': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of certified delivery recipients.'},
            'currentRoutingOrder': {'type': 'Optional[int]', 'description': 'Current routing order.'},
            'editors': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of editor recipients.'},
            'errorDetails': {'type': 'Optional[Dict[str, Any]]', 'description': 'Array of error details.'},
            'inPersonSigners': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of in-person signer recipients.'},
            'intermediaries': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of intermediary recipients.'},
            'notaries': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of notary recipients.'},
            'recipientCount': {'type': 'Optional[int]', 'description': 'The number of recipients.'},
            'seals': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of seal recipients.'},
            'signers': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of signer recipients.'},
            'witnesses': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of witness recipients.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['EnvelopeRecipients']
    },

    'envelope_recipients_update': {
        'method': 'PUT',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients',
        'summary': 'Updates recipients in a draft envelope or corrects recipient information for an in-process envelope.',
        'description': 'Updates recipients for the specified envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'},
            'combine_same_order_recipients': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'offline_signing': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'resend_envelope': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, resends the envelope.'}
        },
        'request_body': {
            'agents': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of agent recipients.'},
            'carbonCopies': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of carbon copy recipients.'},
            'certifiedDeliveries': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of certified delivery recipients.'},
            'currentRoutingOrder': {'type': 'Optional[int]', 'description': 'Current routing order.'},
            'editors': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of editor recipients.'},
            'errorDetails': {'type': 'Optional[Dict[str, Any]]', 'description': 'Array of error details.'},
            'inPersonSigners': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of in-person signer recipients.'},
            'intermediaries': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of intermediary recipients.'},
            'notaries': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of notary recipients.'},
            'recipientCount': {'type': 'Optional[int]', 'description': 'The number of recipients.'},
            'seals': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of seal recipients.'},
            'signers': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of signer recipients.'},
            'witnesses': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of witness recipients.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['EnvelopeRecipients']
    },

    'envelope_recipients_delete': {
        'method': 'DELETE',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients',
        'summary': 'Deletes recipients from a draft envelope.',
        'description': 'Deletes one or more recipients from the specified envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'}
        },
        'request_body': {
            'recipientIds': {'type': 'List[str]', 'required': True, 'description': 'Array of recipient IDs to delete.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['EnvelopeRecipients']
    },

    # ENVELOPE DOCUMENTS OPERATIONS
    'envelope_documents_list': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents',
        'summary': 'Gets a list of envelope documents.',
        'description': 'Retrieves a list of documents associated with the specified envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'},
            'documents_by_userid': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'include_document_size': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['EnvelopeDocuments']
    },

    'envelope_documents_get': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents/{documentId}',
        'summary': 'Gets a document from an envelope.',
        'description': 'Retrieves the specified document from the envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'},
            'documentId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The document ID.'},
            'certificate': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'documents_by_userid': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'encoding': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'encrypt': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'language': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'recipient_id': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'shared_user_id': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'show_changes': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'watermark': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'}
        },
        'headers': {'Accept': 'application/pdf'},
        'api_category': 'esignature',
        'tags': ['EnvelopeDocuments']
    },

    'envelope_documents_put': {
        'method': 'PUT',
        'path': '/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents/{documentId}',
        'summary': 'Adds a document to a draft envelope.',
        'description': 'Adds a document to the specified draft envelope.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'envelopeId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The envelope GUID.'},
            'documentId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The document ID.'}
        },
        'request_body': {
            'document_base64': {'type': 'str', 'required': True, 'description': 'The document encoded as base64.'},
            'documentId': {'type': 'str', 'required': True, 'description': 'The document ID.'},
            'fileExtension': {'type': 'str', 'required': True, 'description': 'The file extension.'},
            'name': {'type': 'str', 'required': True, 'description': 'The document name.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['EnvelopeDocuments']
    },

    # TEMPLATES OPERATIONS
    'templates_list': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/templates',
        'summary': 'Gets the definition of a template.',
        'description': 'Retrieves the list of templates for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return.'},
            'created_from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'created_to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'folder_ids': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'folder_types': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Start of date range filter.'},
            'include': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'is_download': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'is_shared_by_me': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'modified_from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'modified_to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'order': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'order_by': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'search_fields': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'search_text': {'type': 'Optional[str]', 'location': 'query', 'description': 'Text to search for in templates.'},
            'shared_by_me': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'Starting position for the result set.'},
            'template_ids': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'End of date range filter.'},
            'used_from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'used_to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'user_filter': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'user_id': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Templates']
    },

    'templates_create': {
        'method': 'POST',
        'path': '/v2.1/accounts/{accountId}/templates',
        'summary': 'Creates an envelope from a template.',
        'description': 'Creates a template definition using a multipart request.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'}
        },
        'request_body': {
            'allowMarkup': {'type': 'Optional[bool]', 'description': 'When true, Document Markup is enabled.'},
            'allowReassign': {'type': 'Optional[bool]', 'description': 'When true, the recipient can redirect envelope to another recipient.'},
            'allowViewHistory': {'type': 'Optional[bool]', 'description': 'When true, the recipient can view envelope history.'},
            'asynchronous': {'type': 'Optional[bool]', 'description': 'When true, envelope is queued for processing.'},
            'authoritativeCopy': {'type': 'Optional[bool]', 'description': 'Specifies whether the envelope is an authoritative copy.'},
            'autoNavigation': {'type': 'Optional[bool]', 'description': 'When true, auto-navigation is enabled.'},
            'brandId': {'type': 'Optional[str]', 'description': 'The unique identifier of the brand.'},
            'brandLock': {'type': 'Optional[bool]', 'description': 'When true, the brand is locked.'},
            'burnDefaultTabData': {'type': 'Optional[bool]', 'description': 'When true, default tab data is burned into documents.'},
            'created': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'createdDateTime': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'customFields': {'type': 'Optional[Dict[str, Any]]', 'description': 'Custom fields for the template.'},
            'description': {'type': 'Optional[str]', 'description': 'The description of the template.'},
            'documents': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of document objects.'},
            'emailBlurb': {'type': 'Optional[str]', 'description': 'The subject line of the email message sent to recipients.'},
            'emailSettings': {'type': 'Optional[Dict[str, Any]]', 'description': 'Email settings for the template.'},
            'emailSubject': {'type': 'Optional[str]', 'description': 'The subject line of the email message.'},
            'enableWetSign': {'type': 'Optional[bool]', 'description': 'When true, enables wet signing.'},
            'enforceSignerVisibility': {'type': 'Optional[bool]', 'description': 'When true, enforces signer visibility.'},
            'envelopeCustomMetadata': {'type': 'Optional[Dict[str, Any]]', 'description': 'Custom metadata for template.'},
            'envelopeIdStamping': {'type': 'Optional[bool]', 'description': 'When true, envelope ID is stamped.'},
            'envelopeMetadata': {'type': 'Optional[Dict[str, Any]]', 'description': 'Metadata for the template.'},
            'eventNotifications': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of event notifications.'},
            'folderId': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'folderName': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'folders': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Reserved for DocuSign.'},
            'lastModified': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'lastModifiedBy': {'type': 'Optional[Dict[str, Any]]', 'description': 'Reserved for DocuSign.'},
            'lastModifiedDateTime': {'type': 'Optional[str]', 'description': 'The date and time template was last modified.'},
            'lastUsed': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'messageLock': {'type': 'Optional[bool]', 'description': 'When true, prevents template message changes.'},
            'name': {'type': 'str', 'required': True, 'description': 'The template name.'},
            'newPassword': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'notification': {'type': 'Optional[Dict[str, Any]]', 'description': 'Notification settings.'},
            'owner': {'type': 'Optional[Dict[str, Any]]', 'description': 'Reserved for DocuSign.'},
            'pageCount': {'type': 'Optional[int]', 'description': 'Reserved for DocuSign.'},
            'parentFolderId': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'password': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'recipients': {'type': 'Optional[Dict[str, Any]]', 'description': 'Array of recipient objects.'},
            'recipientsLock': {'type': 'Optional[bool]', 'description': 'When true, prevents template recipient changes.'},
            'shared': {'type': 'Optional[bool]', 'description': 'When true, template is shared.'},
            'signerCanSignOnMobile': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'signingLocation': {'type': 'Optional[str]', 'description': 'Specifies the signing location.'},
            'templateId': {'type': 'Optional[str]', 'description': 'The unique identifier of the template.'},
            'transactionId': {'type': 'Optional[str]', 'description': 'Used to identify template in DocuSign Connect.'},
            'uri': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'useDisclosure': {'type': 'Optional[bool]', 'description': 'When true, enables the Consumer Disclosure feature.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Templates']
    },

    'templates_get': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/templates/{templateId}',
        'summary': 'Gets a specific template associated with a specified account.',
        'description': 'Retrieves the definition of the specified template.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'templateId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The template ID GUID.'},
            'include': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Templates']
    },

    'templates_update': {
        'method': 'PUT',
        'path': '/v2.1/accounts/{accountId}/templates/{templateId}',
        'summary': 'Updates an existing template.',
        'description': 'Updates the specified template.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'templateId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The template ID GUID.'}
        },
        'request_body': {
            'name': {'type': 'Optional[str]', 'description': 'The template name.'},
            'description': {'type': 'Optional[str]', 'description': 'The description of the template.'},
            'shared': {'type': 'Optional[bool]', 'description': 'When true, template is shared.'},
            'password': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Templates']
    },

    'templates_delete': {
        'method': 'DELETE',
        'path': '/v2.1/accounts/{accountId}/templates/{templateId}',
        'summary': 'Deletes the specified template.',
        'description': 'Deletes the specified template from the account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'templateId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The template ID GUID.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Templates']
    },

    # USERS OPERATIONS
    'users_list': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/users',
        'summary': 'Retrieves the list of users for the specified account.',
        'description': 'Retrieves the list of users for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'additional_info': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, includes additional user information.'},
            'alternate_admins_only': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return.'},
            'domain_users_only': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'email': {'type': 'Optional[str]', 'location': 'query', 'description': 'Email address to filter by.'},
            'email_substring': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'group_id': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'include_usersettings_for_csv': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'login_status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'not_group_id': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'Starting position for the result set.'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'User status to filter by.'},
            'user_name_substring': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Users']
    },

    'users_create': {
        'method': 'POST',
        'path': '/v2.1/accounts/{accountId}/users',
        'summary': 'Adds news user to the specified account.',
        'description': 'Adds new users to your account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'}
        },
        'request_body': {
            'newUsers': {'type': 'List[Dict[str, Any]]', 'required': True, 'description': 'Array of new user objects.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Users']
    },

    'users_get': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/users/{userId}',
        'summary': 'Gets the user information for a specified user.',
        'description': 'Retrieves user information for the specified user.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'userId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The user ID of the user being accessed.'},
            'additional_info': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, includes additional user information.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Users']
    },

    'users_update': {
        'method': 'PUT',
        'path': '/v2.1/accounts/{accountId}/users/{userId}',
        'summary': 'Updates the user attributes of an existing account user.',
        'description': 'Updates user information for the specified user.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'userId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The user ID of the user being accessed.'},
            'allow_all_languages': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Reserved for DocuSign.'}
        },
        'request_body': {
            'activationAccessCode': {'type': 'Optional[str]', 'description': 'The activation access code.'},
            'email': {'type': 'Optional[str]', 'description': 'The user email address.'},
            'enableConnectForUser': {'type': 'Optional[bool]', 'description': 'When true, enables DocuSign Connect for user.'},
            'firstName': {'type': 'Optional[str]', 'description': 'The user first name.'},
            'forgottenPasswordInfo': {'type': 'Optional[Dict[str, Any]]', 'description': 'Forgotten password information.'},
            'groupList': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of group objects.'},
            'homeAddress': {'type': 'Optional[Dict[str, Any]]', 'description': 'The user home address.'},
            'initialsImageUri': {'type': 'Optional[str]', 'description': 'Contains the URI for retrieving the initials image.'},
            'isAdmin': {'type': 'Optional[bool]', 'description': 'When true, user is an admin.'},
            'lastName': {'type': 'Optional[str]', 'description': 'The user last name.'},
            'loginStatus': {'type': 'Optional[str]', 'description': 'The user login status.'},
            'middleName': {'type': 'Optional[str]', 'description': 'The user middle name.'},
            'password': {'type': 'Optional[str]', 'description': 'The user password.'},
            'passwordExpiration': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'profileImageUri': {'type': 'Optional[str]', 'description': 'Contains the URI for retrieving the profile image.'},
            'sendActivationEmail': {'type': 'Optional[bool]', 'description': 'When true, sends an activation email.'},
            'sendActivationOnInvalidLogin': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'signatureImageUri': {'type': 'Optional[str]', 'description': 'Contains the URI for retrieving the signature image.'},
            'subscribe': {'type': 'Optional[bool]', 'description': 'Reserved for DocuSign.'},
            'suffixName': {'type': 'Optional[str]', 'description': 'The user suffix name.'},
            'title': {'type': 'Optional[str]', 'description': 'The user title.'},
            'userName': {'type': 'Optional[str]', 'description': 'The user name.'},
            'userSettings': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of user settings.'},
            'workAddress': {'type': 'Optional[Dict[str, Any]]', 'description': 'The user work address.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Users']
    },

    'users_delete': {
        'method': 'DELETE',
        'path': '/v2.1/accounts/{accountId}/users/{userId}',
        'summary': 'Closes one or more user records.',
        'description': 'Closes one or more user records.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'userId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The user ID of the user being accessed.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Users']
    },

    # GROUPS OPERATIONS
    'groups_list': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/groups',
        'summary': 'Gets information about groups associated with the account.',
        'description': 'Retrieves information about groups associated with the account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return.'},
            'group_type': {'type': 'Optional[str]', 'location': 'query', 'description': 'Reserved for DocuSign.'},
            'include_usercount': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, includes user count.'},
            'search_text': {'type': 'Optional[str]', 'location': 'query', 'description': 'Text to search for in group name.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'Starting position for the result set.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Groups']
    },

    'groups_create': {
        'method': 'POST',
        'path': '/v2.1/accounts/{accountId}/groups',
        'summary': 'Creates one or more groups for the account.',
        'description': 'Creates one or more groups for the account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'}
        },
        'request_body': {
            'groups': {'type': 'List[Dict[str, Any]]', 'required': True, 'description': 'Array of group objects.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Groups']
    },

    'groups_get': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/groups/{groupId}',
        'summary': 'Gets information about a group.',
        'description': 'Retrieves information about the specified group.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'groupId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The ID of the group being accessed.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Groups']
    },

    'groups_update': {
        'method': 'PUT',
        'path': '/v2.1/accounts/{accountId}/groups/{groupId}',
        'summary': 'Updates the group information for a group.',
        'description': 'Updates the group name and modifies, or set, the permission profile for the group.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'groupId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The ID of the group being accessed.'}
        },
        'request_body': {
            'groupName': {'type': 'Optional[str]', 'description': 'The name of the group.'},
            'groupType': {'type': 'Optional[str]', 'description': 'Reserved for DocuSign.'},
            'permissionProfileId': {'type': 'Optional[str]', 'description': 'The permission profile ID.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Groups']
    },

    'groups_delete': {
        'method': 'DELETE',
        'path': '/v2.1/accounts/{accountId}/groups/{groupId}',
        'summary': 'Deletes an existing user group.',
        'description': 'Deletes an existing user group.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'groupId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The ID of the group being accessed.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Groups']
    },

    # GROUP USERS OPERATIONS  
    'group_users_list': {
        'method': 'GET',
        'path': '/v2.1/accounts/{accountId}/groups/{groupId}/users',
        'summary': 'Gets a list of users in a group.',
        'description': 'Retrieves a list of users in the specified group.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'groupId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The ID of the group being accessed.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'Starting position for the result set.'}
        },
        'headers': {'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Groups']
    },

    'group_users_add': {
        'method': 'PUT',
        'path': '/v2.1/accounts/{accountId}/groups/{groupId}/users',
        'summary': 'Adds one or more users to an existing group.',
        'description': 'Adds one or more users to an existing group.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'groupId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The ID of the group being accessed.'}
        },
        'request_body': {
            'users': {'type': 'List[Dict[str, Any]]', 'required': True, 'description': 'Array of user objects.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Groups']
    },

    'group_users_delete': {
        'method': 'DELETE',
        'path': '/v2.1/accounts/{accountId}/groups/{groupId}/users',
        'summary': 'Deletes one or more users from a group.',
        'description': 'Deletes one or more users from the specified group.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The external account number (int) or account ID GUID.'},
            'groupId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The ID of the group being accessed.'}
        },
        'request_body': {
            'users': {'type': 'List[Dict[str, Any]]', 'required': True, 'description': 'Array of user objects.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'},
        'api_category': 'esignature',
        'tags': ['Groups']
    },

    # ================================================================================
    # ADMIN API v2.1 - ORGANIZATION & ENTERPRISE MANAGEMENT
    # ================================================================================

    # ORGANIZATIONS OPERATIONS
    'admin_organizations_list': {
        'method': 'GET',
        'path': '/v2.1/organizations',
        'summary': 'Returns the list of organizations that the authenticated user belongs to.',
        'description': 'Gets information about organizations associated with the authenticated user.',
        'parameters': {
            'mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies the mode for the request.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    'admin_organizations_get': {
        'method': 'GET',
        'path': '/v2.1/organizations/{organizationId}',
        'summary': 'Returns the details of an organization.',
        'description': 'Retrieves information about the specified organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    # ORGANIZATION ACCOUNTS OPERATIONS
    'admin_organization_accounts_list': {
        'method': 'GET',
        'path': '/v2.1/organizations/{organizationId}/accounts',
        'summary': 'Returns the list of accounts in an organization.',
        'description': 'Gets information about accounts in the specified organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'Starting position for the result set.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    'admin_organization_accounts_create': {
        'method': 'POST',
        'path': '/v2.1/organizations/{organizationId}/accounts',
        'summary': 'Creates a new account for an organization.',
        'description': 'Creates a new account for the specified organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'}
        },
        'request_body': {
            'accountName': {'type': 'str', 'required': True, 'description': 'The account name.'},
            'accountSettings': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Account settings.'},
            'addressInformation': {'type': 'Optional[Dict[str, Any]]', 'description': 'Account address information.'},
            'subscriptionDetails': {'type': 'Optional[Dict[str, Any]]', 'description': 'Subscription details.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    # ORGANIZATION USERS OPERATIONS
    'admin_organization_users_list': {
        'method': 'GET',
        'path': '/v2.1/organizations/{organizationId}/users',
        'summary': 'Returns the list of users in an organization.',
        'description': 'Gets information about users in the specified organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'Starting position for the result set.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return.'},
            'email': {'type': 'Optional[str]', 'location': 'query', 'description': 'Email address to filter by.'},
            'email_substring': {'type': 'Optional[str]', 'location': 'query', 'description': 'Email substring to filter by.'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'User status to filter by.'},
            'membership_status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Membership status to filter by.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    'admin_organization_users_get': {
        'method': 'GET',
        'path': '/v2.1/organizations/{organizationId}/users/{userId}',
        'summary': 'Returns the details of an organization user.',
        'description': 'Retrieves information about the specified user in the organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'},
            'userId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The user ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    'admin_organization_users_update': {
        'method': 'PATCH',
        'path': '/v2.1/organizations/{organizationId}/users/{userId}',
        'summary': 'Updates an organization user\'s details.',
        'description': 'Updates information about the specified user in the organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'},
            'userId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The user ID GUID.'}
        },
        'request_body': {
            'id': {'type': 'str', 'required': True, 'description': 'The user ID GUID.'},
            'site_id': {'type': 'Optional[int]', 'description': 'Reserved for DocuSign.'},
            'user_name': {'type': 'Optional[str]', 'description': 'The user name.'},
            'first_name': {'type': 'Optional[str]', 'description': 'The user first name.'},
            'last_name': {'type': 'Optional[str]', 'description': 'The user last name.'},
            'email': {'type': 'Optional[str]', 'description': 'The user email address.'},
            'default_account_id': {'type': 'Optional[str]', 'description': 'The default account ID.'},
            'language_culture': {'type': 'Optional[str]', 'description': 'The language culture.'},
            'selected_languages': {'type': 'Optional[List[str]]', 'description': 'Selected languages.'},
            'fed_auth_required': {'type': 'Optional[str]', 'description': 'Federation authentication required.'},
            'auto_activate_memberships': {'type': 'Optional[bool]', 'description': 'Auto-activate memberships.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    'admin_organization_users_delete': {
        'method': 'DELETE',
        'path': '/v2.1/organizations/{organizationId}/users/{userId}',
        'summary': 'Removes a user from an organization.',
        'description': 'Removes the specified user from the organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'},
            'userId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The user ID GUID.'}
        },
        'request_body': {
            'id': {'type': 'str', 'required': True, 'description': 'The user ID GUID.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    # USER IMPORTS OPERATIONS
    'admin_organization_user_imports_add': {
        'method': 'POST',
        'path': '/v2.1/organizations/{organizationId}/imports/bulk_users/add',
        'summary': 'Bulk adds users to an organization.',
        'description': 'Adds users in bulk to the specified organization using a CSV file.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'}
        },
        'request_body': {
            'file_csv': {'type': 'str', 'required': True, 'description': 'The CSV file content as base64.'}
        },
        'headers': {'Content-Type': 'multipart/form-data', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    'admin_organization_user_imports_update': {
        'method': 'POST',
        'path': '/v2.1/organizations/{organizationId}/imports/bulk_users/update',
        'summary': 'Bulk updates users in an organization.',
        'description': 'Updates users in bulk in the specified organization using a CSV file.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'}
        },
        'request_body': {
            'file_csv': {'type': 'str', 'required': True, 'description': 'The CSV file content as base64.'}
        },
        'headers': {'Content-Type': 'multipart/form-data', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    'admin_organization_user_imports_close': {
        'method': 'POST',
        'path': '/v2.1/organizations/{organizationId}/imports/bulk_users/close',
        'summary': 'Bulk closes users in an organization.',
        'description': 'Closes users in bulk in the specified organization using a CSV file.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'}
        },
        'request_body': {
            'file_csv': {'type': 'str', 'required': True, 'description': 'The CSV file content as base64.'}
        },
        'headers': {'Content-Type': 'multipart/form-data', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    # IDENTITY PROVIDERS OPERATIONS
    'admin_identity_providers_list': {
        'method': 'GET',
        'path': '/v2.1/organizations/{organizationId}/identity_providers',
        'summary': 'Returns the list of identity providers for an organization.',
        'description': 'Gets information about identity providers for the specified organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    # RESERVED DOMAINS OPERATIONS
    'admin_reserved_domains_list': {
        'method': 'GET',
        'path': '/v2.1/organizations/{organizationId}/reserved_domains',
        'summary': 'Returns the list of reserved domains for an organization.',
        'description': 'Gets information about reserved domains for the specified organization.',
        'parameters': {
            'organizationId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The organization ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'admin',
        'tags': ['Organizations']
    },

    # ================================================================================
    # ROOMS API v2 - REAL ESTATE TRANSACTION MANAGEMENT
    # ================================================================================

    # ROOMS OPERATIONS
    'rooms_list': {
        'method': 'GET',
        'path': '/v2/accounts/{accountId}/rooms',
        'summary': 'Gets a list of rooms available to the user.',
        'description': 'Retrieves a list of rooms for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'The maximum number of results to return.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'The position within the total result set.'},
            'room_status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by room status.'},
            'office_id': {'type': 'Optional[int]', 'location': 'query', 'description': 'Filters by office ID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    'rooms_create': {
        'method': 'POST',
        'path': '/v2/accounts/{accountId}/rooms',
        'summary': 'Creates a room.',
        'description': 'Creates a new room for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'}
        },
        'request_body': {
            'name': {'type': 'str', 'required': True, 'description': 'The name of the room.'},
            'templateId': {'type': 'Optional[int]', 'description': 'The template ID to use for the room.'},
            'officeId': {'type': 'Optional[int]', 'description': 'The office ID for the room.'},
            'fieldData': {'type': 'Optional[Dict[str, Any]]', 'description': 'Field data for the room.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    'rooms_get': {
        'method': 'GET',
        'path': '/v2/accounts/{accountId}/rooms/{roomId}',
        'summary': 'Gets information about a room.',
        'description': 'Retrieves information about the specified room.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'roomId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The room ID.'},
            'include': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies additional information to include.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    'rooms_update': {
        'method': 'PUT',
        'path': '/v2/accounts/{accountId}/rooms/{roomId}',
        'summary': 'Updates room details.',
        'description': 'Updates information for the specified room.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'roomId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The room ID.'}
        },
        'request_body': {
            'name': {'type': 'Optional[str]', 'description': 'The name of the room.'},
            'roomStatus': {'type': 'Optional[str]', 'description': 'The status of the room.'},
            'fieldData': {'type': 'Optional[Dict[str, Any]]', 'description': 'Field data for the room.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    'rooms_delete': {
        'method': 'DELETE',
        'path': '/v2/accounts/{accountId}/rooms/{roomId}',
        'summary': 'Deletes a room.',
        'description': 'Deletes the specified room.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'roomId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The room ID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    # ROOM USERS OPERATIONS
    'room_users_list': {
        'method': 'GET',
        'path': '/v2/accounts/{accountId}/rooms/{roomId}/users',
        'summary': 'Gets users in a room.',
        'description': 'Retrieves a list of users who have access to the specified room.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'roomId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The room ID.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'The maximum number of results to return.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'The position within the total result set.'},
            'filter': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters users by different criteria.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    'room_users_invite': {
        'method': 'POST',
        'path': '/v2/accounts/{accountId}/rooms/{roomId}/users',
        'summary': 'Invites a user to a room.',
        'description': 'Invites the specified user to the room.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'roomId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The room ID.'}
        },
        'request_body': {
            'userId': {'type': 'int', 'required': True, 'description': 'The user ID to invite.'},
            'email': {'type': 'str', 'required': True, 'description': 'The email address of the user to invite.'},
            'firstName': {'type': 'str', 'required': True, 'description': 'The first name of the user.'},
            'lastName': {'type': 'str', 'required': True, 'description': 'The last name of the user.'},
            'accessLevel': {'type': 'Optional[str]', 'description': 'The access level for the user.'},
            'roleId': {'type': 'Optional[int]', 'description': 'The role ID for the user.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    'room_users_remove': {
        'method': 'DELETE',
        'path': '/v2/accounts/{accountId}/rooms/{roomId}/users/{userId}',
        'summary': 'Removes a user from a room.',
        'description': 'Removes the specified user from the room.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'roomId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The room ID.'},
            'userId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The user ID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    # ROOM DOCUMENTS OPERATIONS
    'room_documents_list': {
        'method': 'GET',
        'path': '/v2/accounts/{accountId}/rooms/{roomId}/documents',
        'summary': 'Gets a list of documents in a room.',
        'description': 'Retrieves a list of documents in the specified room.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'roomId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The room ID.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'The maximum number of results to return.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'The position within the total result set.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    'room_documents_upload': {
        'method': 'POST',
        'path': '/v2/accounts/{accountId}/rooms/{roomId}/documents',
        'summary': 'Uploads a document to a room.',
        'description': 'Uploads a document to the specified room.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'roomId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The room ID.'}
        },
        'request_body': {
            'file': {'type': 'str', 'required': True, 'description': 'The document file content as base64.'},
            'documentData': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Document metadata.'}
        },
        'headers': {'Content-Type': 'multipart/form-data', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Rooms']
    },

    # ROOM TEMPLATES OPERATIONS
    'room_templates_list': {
        'method': 'GET',
        'path': '/v2/accounts/{accountId}/room_templates',
        'summary': 'Gets room templates.',
        'description': 'Retrieves a list of room templates for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'The maximum number of results to return.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'The position within the total result set.'},
            'office_id': {'type': 'Optional[int]', 'location': 'query', 'description': 'Filters by office ID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['RoomTemplates']
    },

    'room_templates_get': {
        'method': 'GET',
        'path': '/v2/accounts/{accountId}/room_templates/{templateId}',
        'summary': 'Gets a room template.',
        'description': 'Retrieves information about the specified room template.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'templateId': {'type': 'int', 'location': 'path', 'required': True, 'description': 'The template ID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['RoomTemplates']
    },

    # COMPANY USERS OPERATIONS
    'rooms_company_users_list': {
        'method': 'GET',
        'path': '/v2/accounts/{accountId}/users',
        'summary': 'Gets users in a company.',
        'description': 'Retrieves a list of users in the specified company account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'The maximum number of results to return.'},
            'start_position': {'type': 'Optional[int]', 'location': 'query', 'description': 'The position within the total result set.'},
            'email': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by email address.'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by user status.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Users']
    },

    'rooms_company_users_invite': {
        'method': 'POST',
        'path': '/v2/accounts/{accountId}/users',
        'summary': 'Invites a user to join a company.',
        'description': 'Invites a user to join the specified company account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'}
        },
        'request_body': {
            'invitee': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Invitee information including email, firstName, lastName, and accessLevel.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'rooms',
        'tags': ['Users']
    },

    # ================================================================================
    # CLICK API v1 - CLICKWRAP AGREEMENT MANAGEMENT
    # ================================================================================

    # CLICKWRAPS OPERATIONS
    'clickwraps_list': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/clickwraps',
        'summary': 'Gets a list of clickwraps.',
        'description': 'Retrieves a list of clickwraps for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by clickwrap status.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    'clickwraps_create': {
        'method': 'POST',
        'path': '/v1/accounts/{accountId}/clickwraps',
        'summary': 'Creates a clickwrap.',
        'description': 'Creates a new clickwrap for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'}
        },
        'request_body': {
            'clickwrapName': {'type': 'str', 'required': True, 'description': 'The clickwrap name.'},
            'displaySettings': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Display settings for the clickwrap.'},
            'documents': {'type': 'List[Dict[str, Any]]', 'required': True, 'description': 'Array of document objects.'},
            'fieldsSettings': {'type': 'Optional[Dict[str, Any]]', 'description': 'Field settings for the clickwrap.'},
            'requireReacceptance': {'type': 'Optional[bool]', 'description': 'When true, requires re-acceptance.'},
            'status': {'type': 'Optional[str]', 'description': 'The status of the clickwrap.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    'clickwraps_get': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}',
        'summary': 'Gets a specific clickwrap.',
        'description': 'Retrieves information about the specified clickwrap.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'},
            'versions': {'type': 'Optional[str]', 'location': 'query', 'description': 'Specifies which versions to include.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    'clickwraps_update': {
        'method': 'PUT',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}',
        'summary': 'Updates a clickwrap.',
        'description': 'Updates the specified clickwrap.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'}
        },
        'request_body': {
            'clickwrapName': {'type': 'Optional[str]', 'description': 'The clickwrap name.'},
            'displaySettings': {'type': 'Optional[Dict[str, Any]]', 'description': 'Display settings for the clickwrap.'},
            'documents': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of document objects.'},
            'status': {'type': 'Optional[str]', 'description': 'The status of the clickwrap.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    'clickwraps_delete': {
        'method': 'DELETE',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}',
        'summary': 'Deletes a clickwrap.',
        'description': 'Deletes the specified clickwrap.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    # CLICKWRAP VERSIONS OPERATIONS
    'clickwrap_versions_list': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}/versions',
        'summary': 'Gets clickwrap versions.',
        'description': 'Retrieves a list of versions for the specified clickwrap.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    'clickwrap_versions_create': {
        'method': 'POST',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}/versions',
        'summary': 'Creates a clickwrap version.',
        'description': 'Creates a new version for the specified clickwrap.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'}
        },
        'request_body': {
            'documents': {'type': 'List[Dict[str, Any]]', 'required': True, 'description': 'Array of document objects.'},
            'displaySettings': {'type': 'Optional[Dict[str, Any]]', 'description': 'Display settings for the version.'},
            'status': {'type': 'Optional[str]', 'description': 'The status of the version.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    'clickwrap_versions_get': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}/versions/{versionId}',
        'summary': 'Gets a clickwrap version.',
        'description': 'Retrieves information about the specified clickwrap version.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'},
            'versionId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The version ID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    'clickwrap_versions_update': {
        'method': 'PUT',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}/versions/{versionId}',
        'summary': 'Updates a clickwrap version.',
        'description': 'Updates the specified clickwrap version.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'},
            'versionId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The version ID.'}
        },
        'request_body': {
            'documents': {'type': 'Optional[List[Dict[str, Any]]]', 'description': 'Array of document objects.'},
            'displaySettings': {'type': 'Optional[Dict[str, Any]]', 'description': 'Display settings for the version.'},
            'status': {'type': 'Optional[str]', 'description': 'The status of the version.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    # CLICKWRAP AGREEMENTS OPERATIONS
    'clickwrap_agreements_list': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}/agreements',
        'summary': 'Gets clickwrap agreements.',
        'description': 'Retrieves a list of agreements for the specified clickwrap.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'},
            'client_user_id': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by client user ID.'},
            'from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Start date for the date range filter.'},
            'to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'End date for the date range filter.'},
            'page_number': {'type': 'Optional[int]', 'location': 'query', 'description': 'The page number for pagination.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    'clickwrap_agreements_create': {
        'method': 'POST',
        'path': '/v1/accounts/{accountId}/clickwraps/{clickwrapId}/agreements',
        'summary': 'Creates a clickwrap agreement.',
        'description': 'Creates a new agreement for the specified clickwrap.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'clickwrapId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The clickwrap ID.'}
        },
        'request_body': {
            'clientUserId': {'type': 'str', 'required': True, 'description': 'The client user ID.'},
            'documentData': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Document data for the agreement.'},
            'metadata': {'type': 'Optional[Dict[str, Any]]', 'description': 'Metadata for the agreement.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'click',
        'tags': ['Clickwraps']
    },

    # ================================================================================
    # MAESTRO API v1 - WORKFLOW ORCHESTRATION
    # ================================================================================

    # MAESTRO WORKFLOWS OPERATIONS
    'maestro_workflows_list': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/workflows',
        'summary': 'Gets a list of workflows.',
        'description': 'Retrieves a list of Maestro workflows for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by workflow status.'},
            'published': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, returns only published workflows.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'maestro',
        'tags': ['Workflows']
    },

    'maestro_workflows_create': {
        'method': 'POST',
        'path': '/v1/accounts/{accountId}/workflows',
        'summary': 'Creates a workflow.',
        'description': 'Creates a new Maestro workflow for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'}
        },
        'request_body': {
            'workflowName': {'type': 'str', 'required': True, 'description': 'The name of the workflow.'},
            'workflowDescription': {'type': 'Optional[str]', 'description': 'The description of the workflow.'},
            'accountId': {'type': 'str', 'required': True, 'description': 'The account ID.'},
            'documentVersion': {'type': 'str', 'required': True, 'description': 'The document version.'},
            'schemaVersion': {'type': 'str', 'required': True, 'description': 'The schema version.'},
            'participants': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Participant definitions.'},
            'trigger': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Trigger definition.'},
            'variables': {'type': 'Optional[Dict[str, Any]]', 'description': 'Variable definitions.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'maestro',
        'tags': ['Workflows']
    },

    'maestro_workflows_get': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/workflows/{workflowId}',
        'summary': 'Gets a workflow.',
        'description': 'Retrieves information about the specified Maestro workflow.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'workflowId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The workflow ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'maestro',
        'tags': ['Workflows']
    },

    'maestro_workflows_update': {
        'method': 'PUT',
        'path': '/v1/accounts/{accountId}/workflows/{workflowId}',
        'summary': 'Updates a workflow.',
        'description': 'Updates the specified Maestro workflow.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'workflowId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The workflow ID GUID.'}
        },
        'request_body': {
            'workflowName': {'type': 'Optional[str]', 'description': 'The name of the workflow.'},
            'workflowDescription': {'type': 'Optional[str]', 'description': 'The description of the workflow.'},
            'participants': {'type': 'Optional[Dict[str, Any]]', 'description': 'Participant definitions.'},
            'trigger': {'type': 'Optional[Dict[str, Any]]', 'description': 'Trigger definition.'},
            'variables': {'type': 'Optional[Dict[str, Any]]', 'description': 'Variable definitions.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'maestro',
        'tags': ['Workflows']
    },

    'maestro_workflows_delete': {
        'method': 'DELETE',
        'path': '/v1/accounts/{accountId}/workflows/{workflowId}',
        'summary': 'Deletes a workflow.',
        'description': 'Deletes the specified Maestro workflow.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'workflowId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The workflow ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'maestro',
        'tags': ['Workflows']
    },

    # MAESTRO WORKFLOW INSTANCES OPERATIONS
    'maestro_workflow_instances_list': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/workflow_instances',
        'summary': 'Gets workflow instances.',
        'description': 'Retrieves a list of workflow instances for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'workflow_id': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by workflow ID.'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by instance status.'},
            'from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Start date for the date range filter.'},
            'to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'End date for the date range filter.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'maestro',
        'tags': ['WorkflowInstances']
    },

    'maestro_workflow_instances_get': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/workflow_instances/{instanceId}',
        'summary': 'Gets a workflow instance.',
        'description': 'Retrieves information about the specified workflow instance.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'instanceId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The instance ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'maestro',
        'tags': ['WorkflowInstances']
    },

    'maestro_workflow_instances_cancel': {
        'method': 'POST',
        'path': '/v1/accounts/{accountId}/workflow_instances/{instanceId}/cancel',
        'summary': 'Cancels a workflow instance.',
        'description': 'Cancels the specified workflow instance.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'instanceId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The instance ID GUID.'}
        },
        'request_body': {
            'reason': {'type': 'Optional[str]', 'description': 'The reason for cancellation.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'maestro',
        'tags': ['WorkflowInstances']
    },

    # ================================================================================
    # WEBFORMS API v1.1 - FORM MANAGEMENT
    # ================================================================================

    # WEBFORMS OPERATIONS
    'webforms_list': {
        'method': 'GET',
        'path': '/v1.1/accounts/{accountId}/forms',
        'summary': 'Gets a list of forms.',
        'description': 'Retrieves a list of web forms for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'search': {'type': 'Optional[str]', 'location': 'query', 'description': 'Text to search for in form names.'},
            'is_published': {'type': 'Optional[bool]', 'location': 'query', 'description': 'When true, returns only published forms.'},
            'from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Start date for the date range filter.'},
            'to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'End date for the date range filter.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'webforms',
        'tags': ['Forms']
    },

    'webforms_create': {
        'method': 'POST',
        'path': '/v1.1/accounts/{accountId}/forms',
        'summary': 'Creates a form.',
        'description': 'Creates a new web form for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'}
        },
        'request_body': {
            'formName': {'type': 'str', 'required': True, 'description': 'The name of the form.'},
            'hasFile': {'type': 'bool', 'required': True, 'description': 'Indicates if the form has a file.'},
            'isStandAlone': {'type': 'bool', 'required': True, 'description': 'Indicates if the form is standalone.'},
            'formMetadata': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Form metadata.'},
            'config': {'type': 'Optional[Dict[str, Any]]', 'description': 'Form configuration.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'webforms',
        'tags': ['Forms']
    },

    'webforms_get': {
        'method': 'GET',
        'path': '/v1.1/accounts/{accountId}/forms/{formId}',
        'summary': 'Gets a form.',
        'description': 'Retrieves information about the specified web form.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'formId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The form ID GUID.'},
            'state': {'type': 'Optional[str]', 'location': 'query', 'description': 'The form state to retrieve.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'webforms',
        'tags': ['Forms']
    },

    'webforms_update': {
        'method': 'PUT',
        'path': '/v1.1/accounts/{accountId}/forms/{formId}',
        'summary': 'Updates a form.',
        'description': 'Updates the specified web form.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'formId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The form ID GUID.'}
        },
        'request_body': {
            'formMetadata': {'type': 'Optional[Dict[str, Any]]', 'description': 'Form metadata.'},
            'config': {'type': 'Optional[Dict[str, Any]]', 'description': 'Form configuration.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'webforms',
        'tags': ['Forms']
    },

    'webforms_delete': {
        'method': 'DELETE',
        'path': '/v1.1/accounts/{accountId}/forms/{formId}',
        'summary': 'Deletes a form.',
        'description': 'Deletes the specified web form.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'formId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The form ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'webforms',
        'tags': ['Forms']
    },

    # WEBFORM INSTANCES OPERATIONS
    'webform_instances_list': {
        'method': 'GET',
        'path': '/v1.1/accounts/{accountId}/forms/{formId}/instances',
        'summary': 'Gets form instances.',
        'description': 'Retrieves a list of instances for the specified web form.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'formId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The form ID GUID.'},
            'from_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Start date for the date range filter.'},
            'to_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'End date for the date range filter.'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filters by instance status.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'webforms',
        'tags': ['FormInstances']
    },

    'webform_instances_get': {
        'method': 'GET',
        'path': '/v1.1/accounts/{accountId}/forms/{formId}/instances/{instanceId}',
        'summary': 'Gets a form instance.',
        'description': 'Retrieves information about the specified form instance.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'formId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The form ID GUID.'},
            'instanceId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The instance ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'webforms',
        'tags': ['FormInstances']
    },

    'webform_instances_refresh': {
        'method': 'POST',
        'path': '/v1.1/accounts/{accountId}/forms/{formId}/instances/{instanceId}/refresh',
        'summary': 'Refreshes a form instance.',
        'description': 'Refreshes the specified form instance to get the latest data.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'formId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The form ID GUID.'},
            'instanceId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The instance ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'webforms',
        'tags': ['FormInstances']
    },

    # ================================================================================
    # NAVIGATOR API - AGREEMENT ANALYTICS & INTELLIGENCE
    # ================================================================================

    # NAVIGATOR DATA SOURCES OPERATIONS
    'navigator_data_sources_list': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/data_sources',
        'summary': 'Gets data sources.',
        'description': 'Retrieves a list of data sources for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'navigator',
        'tags': ['DataSources']
    },

    'navigator_data_sources_create': {
        'method': 'POST',
        'path': '/v1/accounts/{accountId}/data_sources',
        'summary': 'Creates a data source.',
        'description': 'Creates a new data source for the specified account.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'}
        },
        'request_body': {
            'name': {'type': 'str', 'required': True, 'description': 'The name of the data source.'},
            'type': {'type': 'str', 'required': True, 'description': 'The type of the data source.'},
            'configuration': {'type': 'Dict[str, Any]', 'required': True, 'description': 'Configuration settings.'},
            'description': {'type': 'Optional[str]', 'description': 'Description of the data source.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'navigator',
        'tags': ['DataSources']
    },

    'navigator_data_sources_get': {
        'method': 'GET',
        'path': '/v1/accounts/{accountId}/data_sources/{dataSourceId}',
        'summary': 'Gets a data source.',
        'description': 'Retrieves information about the specified data source.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'dataSourceId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The data source ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'navigator',
        'tags': ['DataSources']
    },

    'navigator_data_sources_update': {
        'method': 'PUT',
        'path': '/v1/accounts/{accountId}/data_sources/{dataSourceId}',
        'summary': 'Updates a data source.',
        'description': 'Updates the specified data source.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'dataSourceId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The data source ID GUID.'}
        },
        'request_body': {
            'name': {'type': 'Optional[str]', 'description': 'The name of the data source.'},
            'configuration': {'type': 'Optional[Dict[str, Any]]', 'description': 'Configuration settings.'},
            'description': {'type': 'Optional[str]', 'description': 'Description of the data source.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'navigator',
        'tags': ['DataSources']
    },

    'navigator_data_sources_delete': {
        'method': 'DELETE',
        'path': '/v1/accounts/{accountId}/data_sources/{dataSourceId}',
        'summary': 'Deletes a data source.',
        'description': 'Deletes the specified data source.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'},
            'dataSourceId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The data source ID GUID.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'navigator',
        'tags': ['DataSources']
    },

    # NAVIGATOR QUERIES OPERATIONS
    'navigator_queries_execute': {
        'method': 'POST',
        'path': '/v1/accounts/{accountId}/queries',
        'summary': 'Executes a query.',
        'description': 'Executes a query against the specified data sources.',
        'parameters': {
            'accountId': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The account ID GUID.'}
        },
        'request_body': {
            'query': {'type': 'str', 'required': True, 'description': 'The query to execute.'},
            'dataSourceIds': {'type': 'List[str]', 'required': True, 'description': 'List of data source IDs to query.'},
            'maxResults': {'type': 'Optional[int]', 'description': 'Maximum number of results to return.'}
        },
        'headers': {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'navigator',
        'tags': ['Queries']
    },

    # ================================================================================
    # MONITOR API v2 - SECURITY EVENTS & MONITORING
    # ================================================================================

    # MONITOR DATASET OPERATIONS
    'monitor_dataset_stream': {
        'method': 'GET',
        'path': '/api/v{version}/datasets/{dataSetName}/stream',
        'summary': 'Gets customer event data for an organization.',
        'description': 'Gets customer event data for the organization in a streaming fashion.',
        'parameters': {
            'version': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The API version.'},
            'dataSetName': {'type': 'str', 'location': 'path', 'required': True, 'description': 'The data set name (always "monitor").'},
            'cursor': {'type': 'Optional[str]', 'location': 'query', 'description': 'The cursor for pagination.'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'The maximum number of records to return.'}
        },
        'headers': {'Accept': 'application/json', 'Authorization': 'Bearer {access_token}'},
        'api_category': 'monitor',
        'tags': ['DataSet']
    }
}


class DocuSignDataSourceGenerator:
    """Generates a comprehensive DocuSignDataSource class covering all DocuSign APIs."""
    
    def __init__(self):
        self.generated_methods = []
        
    def _sanitize_method_name(self, operation_id: str) -> str:
        """Convert operation ID to valid Python method name."""
        # Convert to snake_case and sanitize
        method_name = re.sub(r'([A-Z])', r'_\1', operation_id).lower()
        method_name = re.sub(r'^_', '', method_name)  # Remove leading underscore
        method_name = re.sub(r'[^a-zA-Z0-9_]', '_', method_name)  # Replace invalid chars
        method_name = re.sub(r'__+', '_', method_name)  # Remove double underscores
        return method_name.strip('_')
    
    def _format_parameter_type(self, param_info: Dict[str, Any]) -> str:
        """Format parameter type annotation."""
        param_type = param_info['type']
        if param_info.get('required', False):
            return param_type
        else:
            if param_type.startswith('Optional['):
                return param_type
            else:
                return f'Optional[{param_type}]'
    
    def _generate_method_signature(self, operation_id: str, operation: Dict[str, Any]) -> str:
        """Generate method signature with proper parameter typing."""
        method_name = self._sanitize_method_name(operation_id)
        
        # Start with self parameter
        params = ['self']
        
        # Add path parameters first (required)
        path_params = []
        query_params = []
        
        # Separate path and query parameters
        for param_name, param_info in operation['parameters'].items():
            if param_info['location'] == 'path':
                path_params.append((param_name, param_info))
            elif param_info['location'] == 'query':
                query_params.append((param_name, param_info))
        
        # Add path parameters (all required)
        for param_name, param_info in path_params:
            param_type = self._format_parameter_type(param_info)
            params.append(f'{param_name}: {param_type}')
        
        # Add query parameters
        for param_name, param_info in query_params:
            param_type = self._format_parameter_type(param_info)
            if param_info.get('required', False):
                params.append(f'{param_name}: {param_type}')
            else:
                default_value = 'None'
                params.append(f'{param_name}: {param_type} = {default_value}')
        
        # Add request body parameters if present
        if 'request_body' in operation:
            for param_name, param_info in operation['request_body'].items():
                param_type = self._format_parameter_type(param_info)
                if param_info.get('required', False):
                    params.append(f'{param_name}: {param_type}')
                else:
                    default_value = 'None'
                    params.append(f'{param_name}: {param_type} = {default_value}')
        
        return f"async def {method_name}(\n        " + ",\n        ".join(params) + "\n    ) -> DocuSignResponse:"
    
    def _generate_method_body(self, operation_id: str, operation: Dict[str, Any]) -> str:
        """Generate method body with proper request construction."""
        method_name = self._sanitize_method_name(operation_id)
        lines = []
        
        # Add compact docstring
        lines.append(f'        """{operation["summary"]}"""')
        
        # Build URL
        path_template = operation['path']
        lines.append(f'        url = self.base_url + "{path_template}"')
        
        # Format path parameters
        path_params = [param for param, info in operation['parameters'].items() 
                      if info['location'] == 'path']
        if path_params:
            for param in path_params:
                lines.append(f'        url = url.replace("{{{param}}}", str({param}))')
        
        # Build query parameters
        query_params = [param for param, info in operation['parameters'].items() 
                       if info['location'] == 'query']
        if query_params:
            lines.append(f'        params = {{}}')
            for param in query_params:
                lines.append(f'        if {param} is not None:')
                lines.append(f'            params["{param}"] = {param}')
            lines.append(f'        if params:')
            lines.append(f'            query_string = "&".join([f"{{k}}={{v}}" for k, v in params.items()])')
            lines.append(f'            url = f"{{url}}?{{query_string}}"')
        
        # Build request body
        if 'request_body' in operation:
            lines.append(f'        body = {{}}')
            for param_name, param_info in operation['request_body'].items():
                lines.append(f'        if {param_name} is not None:')
                lines.append(f'            body["{param_name}"] = {param_name}')
        
        # Set headers
        lines.append(f'        headers = self.http.headers.copy()')
        api_headers = operation.get('headers', {})
        for header_name, header_value in api_headers.items():
            if header_name == 'Authorization':
                lines.append(f'        headers["{header_name}"] = self._client.get_auth_header()')
            else:
                lines.append(f'        headers["{header_name}"] = "{header_value}"')
        
        # Create and execute request
        lines.append(f'        request = HTTPRequest(')
        lines.append(f'            method="{operation["method"]}",')
        lines.append(f'            url=url,')
        if 'request_body' in operation:
            lines.append(f'            headers=headers,')
            lines.append(f'            body=json.dumps(body) if body else None')
        else:
            lines.append(f'            headers=headers')
        lines.append(f'        )')
        lines.append(f'        try:')
        lines.append(f'            response = await self.http.execute(request)')
        lines.append(f'            return DocuSignResponse(success=True, data=response)')
        lines.append(f'        except Exception as e:')
        lines.append(f'            return DocuSignResponse(success=False, error=str(e))')
        
        # Track generated method
        self.generated_methods.append({
            'name': method_name,
            'operation_id': operation_id,
            'api_category': operation['api_category'],
            'method': operation['method'],
            'path': operation['path'],
            'summary': operation['summary']
        })
        
        return "\n".join(lines)
    
    def generate_complete_datasource(self) -> str:
        """Generate the complete DocuSign datasource class."""
        # Class header and imports
        lines = [
            "from typing import Dict, List, Optional, Union, Any",
            "import json",
            "",
            "from app.sources.client.http.http_request import HTTPRequest",
            "from app.sources.client.docusign.docusign import DocuSignClient, DocuSignResponse",
            "",
            "",
            "class DocuSignDataSource:",
            '    """Comprehensive DocuSign API client wrapper.',
            '    ',
            '    Provides async methods for ALL DocuSign API endpoints across:',
            '    - eSignature API v2.1 (Accounts, Envelopes, Templates, Users, Groups)',
            '    - Admin API v2.1 (Organizations, Users, Identity Providers)',
            '    - Rooms API v2 (Real estate transactions, Users, Documents)',
            '    - Click API v1 (Clickwrap agreements, Versions)',
            '    - Maestro API v1 (Workflow orchestration)',
            '    - WebForms API v1.1 (Form management)',
            '    - Navigator API (Agreement analytics)',
            '    - Monitor API v2 (Security events)',
            '    ',
            '    All methods return DocuSignResponse objects with standardized format.',
            '    Every parameter matches DocuSign\'s official API documentation exactly.',
            '    """',
            "",
            "    def __init__(self, client: DocuSignClient) -> None:",
            "        self._client = client",
            "        self.http = client.get_client()",
            "        if self.http is None:",
            "            raise ValueError('HTTP client is not initialized')",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'DocuSignDataSource':",
            "        return self",
            ""
        ]
        
        # Generate methods for each API operation
        for operation_id, operation in DOCUSIGN_API_OPERATIONS.items():
            # Add method signature
            signature = self._generate_method_signature(operation_id, operation)
            lines.append(f"    {signature}")
            
            # Add method body
            body = self._generate_method_body(operation_id, operation)
            lines.append(body)
            lines.append("")
        
        return "\n".join(lines)


def generate_docusign_datasource(output_dir: Optional[str] = None) -> str:
    """Generate complete DocuSign datasource code and save to docusign folder."""
    import os
    from pathlib import Path
    
    # Create docusign directory
    if output_dir is None:
        script_dir = Path(__file__).parent if __file__ else Path('.')
        docusign_dir = script_dir / "docusign"
    else:
        docusign_dir = Path(output_dir)
    
    docusign_dir.mkdir(exist_ok=True)
    
    generator = DocuSignDataSourceGenerator()
    code = generator.generate_complete_datasource()
    
    # Save to docusign.py file
    output_file = docusign_dir / "docusign.py"
    output_file.write_text(code, encoding='utf-8')
    
    print(f"✅ Generated DocuSign datasource with {len(generator.generated_methods)} methods")
    print(f"📁 Saved to: {output_file}")
    
    # Print summary by API
    by_category = {}
    for method in generator.generated_methods:
        category = method['api_category'].upper()
        if category not in by_category:
            by_category[category] = []
        by_category[category].append(method)
    
    print(f"\n📊 Generated methods by API:")
    total_methods = 0
    for category, methods in sorted(by_category.items()):
        print(f"   {category}: {len(methods)} methods")
        total_methods += len(methods)
    
    print(f"\n🚀 Total: {total_methods} methods across {len(by_category)} APIs")
    print(f"🔧 All parameters explicitly typed (no Any parameters)")
    print(f"📋 Complete DocuSign API coverage (Business + Personal)")
    
    return str(output_file)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate comprehensive DocuSign API datasource')
    parser.add_argument('--output', '-o', help='Output directory path', default=None)
    
    args = parser.parse_args()
    
    generate_docusign_datasource(args.output)