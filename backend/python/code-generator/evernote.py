# ruff: noqa
"""
Evernote API Code Generator - CORRECTED VERSION
================================================

Generates comprehensive Evernote DataSource class that wraps the Evernote SDK Thrift clients.

IMPORTANT: Evernote uses Apache Thrift RPC, NOT REST APIs!
- Methods are called directly on Thrift client objects (note_store.listNotebooks())
- No HTTP request building required - the SDK handles that
- Must handle Thrift exceptions (EDAMUserException, EDAMSystemException, EDAMNotFoundException)

Based on: https://github.com/evernote/evernote-sdk-python
API Docs: https://dev.evernote.com/doc/reference/

Generated methods: 93 total (77 NoteStore + 16 UserStore)
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional, Any


class EvernoteAPIDefinition:
    """Complete Evernote API method definitions for Thrift client wrappers."""
    
    @staticmethod
    def get_note_store_methods() -> List[Dict[str, Any]]:
        """All 77 NoteStore methods."""
        return [
            # Sync Methods
            {'name': 'getSyncState', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'getSyncChunk', 'params': ['authenticationToken', 'afterUSN', 'maxEntries', 'fullSyncOnly'], 'required': ['authenticationToken', 'afterUSN', 'maxEntries', 'fullSyncOnly']},
            {'name': 'getFilteredSyncChunk', 'params': ['authenticationToken', 'afterUSN', 'maxEntries', 'filter'], 'required': ['authenticationToken', 'afterUSN', 'maxEntries', 'filter']},
            {'name': 'getLinkedNotebookSyncState', 'params': ['authenticationToken', 'linkedNotebook'], 'required': ['authenticationToken', 'linkedNotebook']},
            {'name': 'getLinkedNotebookSyncChunk', 'params': ['authenticationToken', 'linkedNotebook', 'afterUSN', 'maxEntries', 'fullSyncOnly'], 'required': ['authenticationToken', 'linkedNotebook', 'afterUSN', 'maxEntries', 'fullSyncOnly']},
            
            # Notebook Methods
            {'name': 'listNotebooks', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'listAccessibleBusinessNotebooks', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'getNotebook', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'getDefaultNotebook', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'createNotebook', 'params': ['authenticationToken', 'notebook'], 'required': ['authenticationToken', 'notebook']},
            {'name': 'updateNotebook', 'params': ['authenticationToken', 'notebook'], 'required': ['authenticationToken', 'notebook']},
            {'name': 'expungeNotebook', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            
            # Note Methods
            {'name': 'getNote', 'params': ['authenticationToken', 'guid', 'withContent', 'withResourcesData', 'withResourcesRecognition', 'withResourcesAlternateData'], 'required': ['authenticationToken', 'guid', 'withContent', 'withResourcesData', 'withResourcesRecognition', 'withResourcesAlternateData']},
            {'name': 'getNoteWithResultSpec', 'params': ['authenticationToken', 'guid', 'resultSpec'], 'required': ['authenticationToken', 'guid', 'resultSpec']},
            {'name': 'getNoteApplicationData', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'getNoteApplicationDataEntry', 'params': ['authenticationToken', 'guid', 'key'], 'required': ['authenticationToken', 'guid', 'key']},
            {'name': 'setNoteApplicationDataEntry', 'params': ['authenticationToken', 'guid', 'key', 'value'], 'required': ['authenticationToken', 'guid', 'key', 'value']},
            {'name': 'unsetNoteApplicationDataEntry', 'params': ['authenticationToken', 'guid', 'key'], 'required': ['authenticationToken', 'guid', 'key']},
            {'name': 'getNoteContent', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'getNoteSearchText', 'params': ['authenticationToken', 'guid', 'noteOnly', 'tokenizeForIndexing'], 'required': ['authenticationToken', 'guid', 'noteOnly', 'tokenizeForIndexing']},
            {'name': 'getResourceSearchText', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'getNoteTagNames', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'createNote', 'params': ['authenticationToken', 'note'], 'required': ['authenticationToken', 'note']},
            {'name': 'updateNote', 'params': ['authenticationToken', 'note'], 'required': ['authenticationToken', 'note']},
            {'name': 'deleteNote', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'expungeNote', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'copyNote', 'params': ['authenticationToken', 'noteGuid', 'toNotebookGuid'], 'required': ['authenticationToken', 'noteGuid', 'toNotebookGuid']},
            {'name': 'listNoteVersions', 'params': ['authenticationToken', 'noteGuid'], 'required': ['authenticationToken', 'noteGuid']},
            {'name': 'getNoteVersion', 'params': ['authenticationToken', 'noteGuid', 'updateSequenceNum', 'withResourcesData', 'withResourcesRecognition', 'withResourcesAlternateData'], 'required': ['authenticationToken', 'noteGuid', 'updateSequenceNum', 'withResourcesData', 'withResourcesRecognition', 'withResourcesAlternateData']},
            
            # Search Methods
            {'name': 'findNotes', 'params': ['authenticationToken', 'filter', 'offset', 'maxNotes'], 'required': ['authenticationToken', 'filter', 'offset', 'maxNotes']},
            {'name': 'findNoteOffset', 'params': ['authenticationToken', 'filter', 'guid'], 'required': ['authenticationToken', 'filter', 'guid']},
            {'name': 'findNotesMetadata', 'params': ['authenticationToken', 'filter', 'offset', 'maxNotes', 'resultSpec'], 'required': ['authenticationToken', 'filter', 'offset', 'maxNotes', 'resultSpec']},
            {'name': 'findNoteCounts', 'params': ['authenticationToken', 'filter', 'withTrash'], 'required': ['authenticationToken', 'filter', 'withTrash']},
            {'name': 'findRelated', 'params': ['authenticationToken', 'query', 'resultSpec'], 'required': ['authenticationToken', 'query', 'resultSpec']},
            
            # Tag Methods
            {'name': 'listTags', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'listTagsByNotebook', 'params': ['authenticationToken', 'notebookGuid'], 'required': ['authenticationToken', 'notebookGuid']},
            {'name': 'getTag', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'createTag', 'params': ['authenticationToken', 'tag'], 'required': ['authenticationToken', 'tag']},
            {'name': 'updateTag', 'params': ['authenticationToken', 'tag'], 'required': ['authenticationToken', 'tag']},
            {'name': 'untagAll', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'expungeTag', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            
            # Resource Methods
            {'name': 'getResource', 'params': ['authenticationToken', 'guid', 'withData', 'withRecognition', 'withAttributes', 'withAlternateData'], 'required': ['authenticationToken', 'guid', 'withData', 'withRecognition', 'withAttributes', 'withAlternateData']},
            {'name': 'getResourceApplicationData', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'getResourceApplicationDataEntry', 'params': ['authenticationToken', 'guid', 'key'], 'required': ['authenticationToken', 'guid', 'key']},
            {'name': 'setResourceApplicationDataEntry', 'params': ['authenticationToken', 'guid', 'key', 'value'], 'required': ['authenticationToken', 'guid', 'key', 'value']},
            {'name': 'unsetResourceApplicationDataEntry', 'params': ['authenticationToken', 'guid', 'key'], 'required': ['authenticationToken', 'guid', 'key']},
            {'name': 'updateResource', 'params': ['authenticationToken', 'resource'], 'required': ['authenticationToken', 'resource']},
            {'name': 'getResourceData', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'getResourceByHash', 'params': ['authenticationToken', 'noteGuid', 'contentHash', 'withData', 'withRecognition', 'withAlternateData'], 'required': ['authenticationToken', 'noteGuid', 'contentHash', 'withData', 'withRecognition', 'withAlternateData']},
            {'name': 'getResourceRecognition', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'getResourceAlternateData', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'getResourceAttributes', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            
            # Saved Search Methods
            {'name': 'listSearches', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'getSearch', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'createSearch', 'params': ['authenticationToken', 'search'], 'required': ['authenticationToken', 'search']},
            {'name': 'updateSearch', 'params': ['authenticationToken', 'search'], 'required': ['authenticationToken', 'search']},
            {'name': 'expungeSearch', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            
            # Linked Notebook Methods
            {'name': 'listLinkedNotebooks', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'createLinkedNotebook', 'params': ['authenticationToken', 'linkedNotebook'], 'required': ['authenticationToken', 'linkedNotebook']},
            {'name': 'updateLinkedNotebook', 'params': ['authenticationToken', 'linkedNotebook'], 'required': ['authenticationToken', 'linkedNotebook']},
            {'name': 'expungeLinkedNotebook', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            
            # Sharing Methods
            {'name': 'authenticateToSharedNotebook', 'params': ['shareKey', 'authenticationToken'], 'required': ['shareKey']},
            {'name': 'getSharedNotebookByAuth', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'authenticateToSharedNote', 'params': ['guid', 'noteKey', 'authenticationToken'], 'required': ['guid', 'noteKey']},
            {'name': 'shareNote', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'stopSharingNote', 'params': ['authenticationToken', 'guid'], 'required': ['authenticationToken', 'guid']},
            {'name': 'listSharedNotebooks', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'shareNotebook', 'params': ['authenticationToken', 'sharedNotebook', 'message'], 'required': ['authenticationToken', 'sharedNotebook']},
            {'name': 'createOrUpdateNotebookShares', 'params': ['authenticationToken', 'shareTemplate'], 'required': ['authenticationToken', 'shareTemplate']},
            {'name': 'updateSharedNotebook', 'params': ['authenticationToken', 'sharedNotebook'], 'required': ['authenticationToken', 'sharedNotebook']},
            {'name': 'setNotebookRecipientSettings', 'params': ['authenticationToken', 'notebookGuid', 'recipientSettings'], 'required': ['authenticationToken', 'notebookGuid', 'recipientSettings']},
            {'name': 'getNotebookShares', 'params': ['authenticationToken', 'notebookGuid'], 'required': ['authenticationToken', 'notebookGuid']},
            {'name': 'manageNotebookShares', 'params': ['authenticationToken', 'parameters'], 'required': ['authenticationToken', 'parameters']},
            {'name': 'manageNoteShares', 'params': ['authenticationToken', 'parameters'], 'required': ['authenticationToken', 'parameters']},
            
            # Public/Business Methods
            {'name': 'getPublicNotebook', 'params': ['userId', 'publicUri'], 'required': ['userId', 'publicUri']},
            {'name': 'emailNote', 'params': ['authenticationToken', 'parameters'], 'required': ['authenticationToken', 'parameters']},
            {'name': 'updateNoteIfUsnMatches', 'params': ['authenticationToken', 'note'], 'required': ['authenticationToken', 'note']},
        ]
    
    @staticmethod
    def get_user_store_methods() -> List[Dict[str, Any]]:
        """All 16 UserStore methods."""
        return [
            {'name': 'checkVersion', 'params': ['clientName', 'edamVersionMajor', 'edamVersionMinor'], 'required': ['clientName']},
            {'name': 'getBootstrapInfo', 'params': ['locale'], 'required': ['locale']},
            {'name': 'authenticateLongSession', 'params': ['username', 'password', 'consumerKey', 'consumerSecret', 'deviceIdentifier', 'deviceDescription', 'supportsTwoFactor'], 'required': ['username', 'password', 'consumerKey', 'consumerSecret', 'deviceDescription', 'supportsTwoFactor']},
            {'name': 'completeTwoFactorAuthentication', 'params': ['authenticationToken', 'oneTimeCode', 'deviceIdentifier', 'deviceDescription'], 'required': ['authenticationToken', 'oneTimeCode', 'deviceDescription']},
            {'name': 'revokeLongSession', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'authenticateToBusiness', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'getUser', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'getPublicUserInfo', 'params': ['username'], 'required': ['username']},
            {'name': 'getPremiumInfo', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'getUserUrls', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'inviteToBusiness', 'params': ['authenticationToken', 'emailAddress'], 'required': ['authenticationToken', 'emailAddress']},
            {'name': 'removeFromBusiness', 'params': ['authenticationToken', 'emailAddress'], 'required': ['authenticationToken', 'emailAddress']},
            {'name': 'updateBusinessUserIdentifier', 'params': ['authenticationToken', 'oldEmailAddress', 'newEmailAddress'], 'required': ['authenticationToken', 'oldEmailAddress', 'newEmailAddress']},
            {'name': 'listBusinessUsers', 'params': ['authenticationToken'], 'required': ['authenticationToken']},
            {'name': 'listBusinessInvitations', 'params': ['authenticationToken', 'includeRequestedInvitations'], 'required': ['authenticationToken', 'includeRequestedInvitations']},
            {'name': 'getAccountLimits', 'params': ['serviceLevel'], 'required': ['serviceLevel']},
        ]


class EvernoteCodeGenerator:
    """Generator for Evernote Thrift client wrapper."""
    
    def __init__(self):
        self.generated_methods = []
    
    def _to_snake_case(self, camel: str) -> str:
        """Convert camelCase to snake_case."""
        import re
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', camel)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    
    def _build_param_list(self, params: List[str], required: List[str]) -> str:
        """Build method parameter list."""
        parts = ['self']
        
        # Add required params
        for param in params:
            if param in required:
                parts.append(f'{self._to_snake_case(param)}: str')
        
        # Add optional params
        for param in params:
            if param not in required:
                parts.append(f'{self._to_snake_case(param)}: Optional[str] = None')
        
        return ',\n        '.join(parts)
    
    def _build_thrift_call(self, method_name: str, params: List[str], service: str) -> str:
        """Build the actual Thrift client call."""
        param_names = [self._to_snake_case(p) for p in params]

        # Build arguments, excluding None values
        args_code = []
        for param in param_names:
            args_code.append(f'            if {param} is not None:')
            args_code.append(f'                args.append({param})')

        return f'''        try:
            # Build arguments list
            args = []
{chr(10).join(args_code)}

            # Call Thrift client method
            result = self.{service}.{method_name}(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{{error_type}}: {{str(e)}}"
            )'''
    
    def _generate_method(self, method_def: Dict[str, Any], service: str) -> str:
        """Generate a single wrapper method."""
        method_name = method_def['name']
        snake_name = self._to_snake_case(method_name)
        params = method_def['params']
        required = method_def['required']
        
        param_list = self._build_param_list(params, required)
        thrift_call = self._build_thrift_call(method_name, params, service)
        
        # Generate docstring
        doc_params = '\n        '.join([f'    {self._to_snake_case(p)} ({"required" if p in required else "optional"}): {p}' for p in params])
        
        self.generated_methods.append({
            'name': snake_name,
            'thrift_name': method_name,
            'service': service,
            'params': len(params)
        })
        
        return f'''
    async def {snake_name}(
        {param_list}
    ) -> EvernoteResponse:
        """
        Wrapper for {service}.{method_name}()
        Args:
{doc_params}
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
{thrift_call}
'''
    
    def generate_datasource(self) -> str:
        """Generate complete Evernote DataSource."""
        
        header = '''"""
Evernote DataSource - Thrift Client Wrapper
============================================

Auto-generated wrapper for Evernote SDK Thrift clients.

This wraps the official Evernote SDK (https://github.com/evernote/evernote-sdk-python)
which uses Apache Thrift RPC protocol, NOT REST APIs.

Usage:
    from evernote.api.client import EvernoteClient
    from evernote_data_source import EvernoteDataSource
    
    # Initialize Evernote SDK client
    evernote_client = EvernoteClient(
        token='your_auth_token',
        sandbox=True  # or False for production
    )
    
    # Create datasource wrapper
    datasource = EvernoteDataSource(evernote_client)
    
    # Call methods (async)
    response = await datasource.list_notebooks(authentication_token=token)
    if response.success:
        print(response.data)

Generated Methods:
    - NoteStore: 77 methods
    - UserStore: 16 methods
    - Total: 93 methods
"""

from typing import Any, Dict, List, Optional
from app.sources.client.evernote.evernote import EvernoteClient
from app.sources.client.evernote.evernote import EvernoteResponse

class EvernoteDataSource:
    """
    Comprehensive Evernote API wrapper for Thrift clients.
    Wraps all NoteStore and UserStore methods from the Evernote SDK.
    Handles Thrift exceptions and provides standardized responses.
    Architecture:
        - Uses Evernote SDK Thrift clients (not HTTP requests)
        - Wraps note_store and user_store Thrift objects
        - Converts Thrift objects to Python dicts
        - Handles exceptions gracefully
    Methods:
        NoteStore (77 methods):
            - Sync: getSyncState, getSyncChunk, getFilteredSyncChunk
            - Notebooks: listNotebooks, createNotebook, updateNotebook, etc.
            - Notes: getNote, createNote, updateNote, deleteNote, etc.
            - Tags: listTags, createTag, updateTag, expungeTag, etc.
            - Resources: getResource, updateResource, getResourceData, etc.
            - Search: findNotes, findNotesMetadata, findNoteCounts, etc.
            - Sharing: shareNotebook, shareNote, manageNotebookShares, etc.
        
        UserStore (16 methods):
            - Auth: authenticateLongSession, authenticateToBusiness, etc.
            - User: getUser, getPremiumInfo, getUserUrls, etc.
            - Business: inviteToBusiness, listBusinessUsers, etc.
    """
    
    def __init__(self, evernote_client: EvernoteClient) -> None:
        """
        Initialize with Evernote SDK client.
        Args:
            evernote_client: Instance of EvernoteClient
        """
        self.client = evernote_client
        self.note_store = evernote_client.get_note_store()
        self.user_store = evernote_client.get_user_store()

    def _thrift_to_dict(self, obj: Any) -> Dict[str, Any]:
        """Convert Thrift object to dictionary."""
        if obj is None:
            return None
        
        if hasattr(obj, '__dict__'):
            result = {}
            for key, value in obj.__dict__.items():
                if value is not None:
                    if isinstance(value, list):
                        result[key] = [self._thrift_to_dict(item) for item in value]
                    elif hasattr(value, '__dict__'):
                        result[key] = self._thrift_to_dict(value)
                    else:
                        result[key] = value
            return result
        
        return obj

    def get_client(self) -> EvernoteClient:
        """Get the underlying Evernote client."""
        return self.client

    def get_note_store(self) -> object:
        """Get the NoteStore Thrift client."""
        return self.note_store

    def get_user_store(self) -> object:
        """Get the UserStore Thrift client."""
        return self.user_store
'''
        
        # Generate NoteStore methods
        note_store_methods = EvernoteAPIDefinition.get_note_store_methods()
        for method_def in note_store_methods:
            header += self._generate_method(method_def, 'note_store')
        
        # Generate UserStore methods
        user_store_methods = EvernoteAPIDefinition.get_user_store_methods()
        for method_def in user_store_methods:
            header += self._generate_method(method_def, 'user_store')
        
        return header
    
    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Save generated code to file."""
        if filename is None:
            filename = "evernote_data_source.py"
        
        # Create evernote directory
        script_dir = Path(__file__).parent if __file__ else Path('.')
        evernote_dir = script_dir / 'evernote'
        evernote_dir.mkdir(exist_ok=True)
        
        full_path = evernote_dir / filename
        
        code = self.generate_datasource()
        full_path.write_text(code, encoding='utf-8')
        
        print(f"[SUCCESS] Generated Evernote DataSource: {len(self.generated_methods)} methods")
        print(f"[FILE] {full_path}")
        
        # Summary
        note_store = [m for m in self.generated_methods if m['service'] == 'note_store']
        user_store = [m for m in self.generated_methods if m['service'] == 'user_store']
        
        print(f"\n[SUMMARY]")
        print(f"   - Total methods: {len(self.generated_methods)}")
        print(f"   - NoteStore methods: {len(note_store)}")
        print(f"   - UserStore methods: {len(user_store)}")
        print(f"\n[ARCHITECTURE]")
        print(f"   - Uses: Evernote SDK Thrift clients")
        print(f"   - Protocol: Apache Thrift RPC (NOT REST)")
        print(f"   - Wraps: note_store and user_store objects")
        print(f"   - Handles: Thrift exceptions automatically")


def generate_readme() -> str:
    """Generate README."""
    return '''# Evernote DataSource - Thrift Client Wrapper

Comprehensive Python wrapper for the Evernote SDK Thrift clients.

## IMPORTANT: Architecture

**Evernote uses Apache Thrift RPC, NOT REST APIs!**

- This wrapper uses the official Evernote SDK: https://github.com/evernote/evernote-sdk-python
- Methods call Thrift client objects directly (e.g., `note_store.listNotebooks()`)
- No HTTP request building required - the SDK handles the Thrift protocol
- All 93 methods wrapped (77 NoteStore + 16 UserStore)

## Installation

```bash
pip install evernote3
```

## Quick Start

```python
from evernote.api.client import EvernoteClient
from evernote_data_source import EvernoteDataSource

# Initialize Evernote SDK client
client = EvernoteClient(
    token='your_developer_token',
    sandbox=True  # Use False for production
)

# Create datasource wrapper
datasource = EvernoteDataSource(client)

# List notebooks
response = await datasource.list_notebooks(
    authentication_token='your_token'
)

if response.success:
    notebooks = response.data
    print(f"Found {len(notebooks)} notebooks")
else:
    print(f"Error: {response.error}")
```

## Features

- **93 Methods**: Complete coverage of NoteStore and UserStore APIs
- **Thrift Wrapper**: Properly wraps Evernote SDK Thrift clients
- **Type Safe**: All parameters properly typed
- **Exception Handling**: Catches Thrift exceptions automatically
- **Async Support**: All methods are async-ready
- **Dict Conversion**: Converts Thrift objects to Python dicts

## API Coverage

### NoteStore (77 methods)
- **Sync**: getSyncState, getSyncChunk, getFilteredSyncChunk
- **Notebooks**: listNotebooks, createNotebook, updateNotebook, expungeNotebook
- **Notes**: getNote, createNote, updateNote, deleteNote, copyNote
- **Tags**: listTags, createTag, updateTag, expungeTag
- **Resources**: getResource, updateResource, getResourceData
- **Search**: findNotes, findNotesMetadata, findNoteCounts
- **Sharing**: shareNotebook, shareNote, manageNotebookShares

### UserStore (16 methods)
- **Auth**: authenticateLongSession, authenticateToBusiness
- **User**: getUser, getPremiumInfo, getUserUrls
- **Business**: inviteToBusiness, listBusinessUsers, removeFromBusiness

## Usage Examples

### Create Note

```python
from evernote.edam.type import ttypes as Types

note = Types.Note()
note.title = "My Note"
note.content = '<?xml version="1.0" encoding="UTF-8"?>'
note.content += '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
note.content += '<en-note>Hello World!</en-note>'

response = await datasource.create_note(
    authentication_token=auth_token,
    note=note
)
```

### Search Notes

```python
from evernote.edam.notestore import NoteStore

filter = NoteStore.NoteFilter()
filter.words = "tag:important"

spec = NoteStore.NotesMetadataResultSpec()
spec.includeTitle = True
spec.includeUpdated = True

response = await datasource.find_notes_metadata(
    authentication_token=auth_token,
    filter=filter,
    offset=0,
    maxNotes=50,
    resultSpec=spec
)
```

## Key Differences from REST APIs

| Aspect | REST (Wrong for Evernote) | Thrift (Correct) |
|--------|---------------------------|------------------|
| Protocol | HTTP with URLs | Thrift RPC |
| Method Calls | POST /api/notes | note_store.createNote() |
| Client | HTTPRequest | Thrift Client |
| Data Format | JSON | Thrift binary |
| SDK Required | No | Yes (evernote-sdk-python) |

## Error Handling

```python
response = await datasource.get_note(
    authentication_token=token,
    guid="invalid-guid",
    withContent=True,
    withResourcesData=False,
    withResourcesRecognition=False,
    withResourcesAlternateData=False
)

if not response.success:
    print(f"Error: {response.error}")
    # Error types: EDAMUserException, EDAMSystemException, EDAMNotFoundException
```

## Requirements

- Python 3.7+
- evernote3 (pip install evernote3)
- Evernote Developer Token (https://sandbox.evernote.com/api/DeveloperToken.action)

## Links

- Evernote SDK: https://github.com/evernote/evernote-sdk-python
- API Docs: https://dev.evernote.com/doc/reference/
- Developer Portal: https://dev.evernote.com/
'''


def main():
    """Main generator function."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Generate Evernote Thrift client wrapper',
        epilog='Note: Evernote uses Thrift RPC, not REST!'
    )
    parser.add_argument('--filename', '-f', help='Output filename')
    parser.add_argument('--with-readme', action='store_true', help='Generate README')
    
    args = parser.parse_args()
    
    try:
        print("[START] Evernote DataSource Generation")
        print("[INFO] Architecture: Thrift RPC (NOT REST)")
        print("[INFO] Using: Evernote SDK Thrift clients")
        
        generator = EvernoteCodeGenerator()
        generator.save_to_file(args.filename)
        
        if args.with_readme:
            script_dir = Path(__file__).parent if __file__ else Path('.')
            readme_path = script_dir / 'evernote' / 'README.md'
            readme_path.write_text(generate_readme(), encoding='utf-8')
            print(f"[FILE] {readme_path}")
        
        print(f"\n[SUCCESS] Evernote DataSource generated!")
        print(f"[NOTE] This wraps Thrift clients, not HTTP requests")
        print(f"[NEXT] Install evernote SDK: pip install evernote3")
        
        return 0
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())