# Knowledge Base API Integration

## API Architecture

The Knowledge Base system is divided into two types of APIs:

1. **Get Operations (Knowledge Hub)** - Read-only APIs for fetching and navigating the hierarchical knowledge base structure
2. **Action APIs (KB CRUD)** - Write operations for creating, updating, deleting knowledge bases, folders, and records

---

## Get Operations (Knowledge Hub APIs)

These two APIs handle all read operations for browsing the knowledge hub hierarchy. They are used for navigation, search, and displaying data.

### API 1: Get Knowledge Hub Nodes (Root or Filtered)

**Endpoint:** `GET /api/v1/knowledgeBase/knowledge-hub/nodes`

**Purpose:** Fetch root-level nodes (apps & KBs) or filtered nodes across the entire knowledge hub

**Authentication:** Bearer token required

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `onlyContainers` | boolean | false | Return only nodes with children (folders, apps, recordGroups) - **always true for sidebar** |
| `page` | integer | 1 | Page number for pagination (≥ 1) |
| `limit` | integer | 50 | Items per page (1-200) |
| `include` | string | - | Comma-separated: `counts`, `permissions`, `breadcrumbs`, `availableFilters` |
| `sortBy` | enum | name | Sort field: `name`, `createdAt`, `updatedAt`, `size`, `type` |
| `sortOrder` | enum | asc | Sort direction: `asc`, `desc` |
| `q` | string | - | Full-text search query (2-500 chars) |
| `nodeTypes` | string | - | Filter by node types (comma-separated, max 100) |
| `recordTypes` | string | - | Filter by record types: `FILE`, `WEBPAGE`, `MESSAGE`, `EMAIL`, `TICKET` (comma-separated, max 100) |
| `origins` | string | - | Filter by origin: `KB`, `CONNECTOR` (comma-separated, max 100) |
| `connectorIds` | string | - | Filter by connector UUIDs (comma-separated, max 100) |
| `kbIds` | string | - | Filter by KB UUIDs (comma-separated, max 100) - **used for sidebar navigation** |
| `indexingStatus` | string | - | Filter by status: `COMPLETED`, `IN_PROGRESS`, `FAILED` (comma-separated, max 100) |
| `createdAt` | string | - | Date range filter in epoch ms: `gte:X,lte:Y` |
| `updatedAt` | string | - | Date range filter in epoch ms: `gte:X,lte:Y` |
| `size` | string | - | File size range in bytes: `gte:X,lte:Y` |

**Usage:**
- **Sidebar initial load:** `?page=1&limit=50&include=counts&onlyContainers=true`
- **Sidebar folder expansion:** `?kbIds={folderId}&onlyContainers=true`
- **Global search:** `?q=search+term&page=1&limit=50`

---

### API 2: Get Node Items (Children of Specific Node)

**Endpoint:** `GET /api/v1/knowledgeBase/knowledge-hub/nodes/:nodeType/:nodeId`

**Purpose:** Fetch children of a specific node (folder contents, KB items, app records)

**Authentication:** Bearer token required

**Path Parameters:**

| Parameter | Type | Description | Example Values |
|-----------|------|-------------|----------------|
| `nodeType` | enum | Type of parent node | `app`, `kb`, `folder`, `recordGroup`, `record` |
| `nodeId` | string | UUID of parent node | `f31459bb-c25a-40bd-8be9-8dcafdd01740` |

**Query Parameters:** (Same as API 1)

All parameters from API 1 are available for filtering, sorting, and pagination.

**Usage:**
- **Data area on folder click:** `/nodes/folder/{folderId}?page=1&limit=50&include=counts,permissions,breadcrumbs,availableFilters`
- **Filter within KB:** `/nodes/kb/{kbId}?recordTypes=FILE&sortBy=updatedAt&sortOrder=desc`
- **Sidebar expansion:** `/nodes/app/{appId}?onlyContainers=true&page=1&limit=50`

---

## Action APIs (KB CRUD Operations)

## Action APIs (KB CRUD Operations)

These APIs handle creating, updating, and deleting knowledge bases, folders, and records.

### Knowledge Base Management

#### 3. Create Knowledge Base
**POST** `/api/v1/knowledgeBase/`

**Body:**
```json
{
  "kbName": "string"
}
```

**Example:**
```bash
POST {{esbackend}}/api/v1/knowledgeBase/
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"kbName": "Test"}
```

#### 4. List Knowledge Bases
**GET** `/api/v1/knowledgeBase/`

**Query Parameters:**
- `page`: integer (default: 1)
- `limit`: integer (default: 20, max: 100)
- `search`: string
- `permissions`: comma-separated
- `sortBy`: `name` | `createdAtTimestamp` | `updatedAtTimestamp` | `userRole`
- `sortOrder`: `asc` | `desc`

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgeBase/?page=1&limit=20&sortBy=name&sortOrder=asc
Authorization: Bearer {{authToken}}
```

#### 5. Get Knowledge Base by ID
**GET** `/api/v1/knowledgeBase/:kbId`

**Path Variables:**
- `kbId`: UUID

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgeBase/a77fbf1c-abf2-486d-beac-62459952f001
Authorization: Bearer {{authToken}}
```

#### 6. Update Knowledge Base
**PUT** `/api/v1/knowledgeBase/:kbId`

**Body:**
```json
{
  "kbName": "string"
}
```

**Example:**
```bash
PUT {{esbackend}}/api/v1/knowledgeBase/89811362-002c-40b0-84a8-65e456670665
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"kbName": "New test"}
```

#### 7. Delete Knowledge Base
**DELETE** `/api/v1/knowledgeBase/:kbId`

**Path Variables:**
- `kbId`: UUID

**Example:**
```bash
DELETE {{esbackend}}/api/v1/knowledgeBase/8d8eb590-a16a-44c3-948c-97768660cf6d
Authorization: Bearer {{authToken}}
```

#### 8. Get KB Records (Hierarchical)
**GET** `/api/v1/knowledgeBase/:kbId/records`

**Query Parameters:**
- `page`: integer (default: 1)
- `limit`: integer (default: 20)
- `search`: string

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgeBase/a77fbf1c-abf2-486d-beac-62459952f001/records?page=1&limit=20
Authorization: Bearer {{authToken}}
```

#### 9. Create Root Folder
**POST** `/api/v1/knowledgeBase/:kbId/folder`

**Body:**
```json
{
  "folderName": "string"
}
```

**Example:**
```bash
POST {{esbackend}}/api/v1/knowledgeBase/a77fbf1c-abf2-486d-beac-62459952f001/folder
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"folderName": "test"}
```

#### 10. Create Nested Folder
**POST** `/api/v1/knowledgeBase/:kbId/folder/:folderId/subfolder`

**Body:**
```json
{
  "folderName": "string"
}
```

**Example:**
```bash
POST {{esbackend}}/api/v1/knowledgeBase/a77fbf1c-abf2-486d-beac-62459952f001/folder/77d2cc72/subfolder
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"folderName": "subfolder 2"}
```

#### 11. Get Folder Contents
**GET** `/api/v1/knowledgeBase/:kbId/folder/:folderId`

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgeBase/a77fbf1c-abf2-486d-beac-62459952f001/folder/77d2cc72
Authorization: Bearer {{authToken}}
```

#### 12. Update Folder
**PUT** `/api/v1/knowledgeBase/:kbId/folder/:folderId`

**Body:**
```json
{
  "folderName": "string"
}
```

**Example:**
```bash
PUT {{esbackend}}/api/v1/knowledgeBase/a77fbf1c-abf2-486d-beac-62459952f001/folder/0b190899
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"folderName": "subfolder 2"}
```

#### 13. Delete Folder
**DELETE** `/api/v1/knowledgeBase/:kbId/folder/:folderId`

**Example:**
```bash
DELETE {{esbackend}}/api/v1/knowledgeBase/a77fbf1c-abf2-486d-beac-62459952f001/folder/b1a52ada
Authorization: Bearer {{authToken}}
```

#### 14. Upload Records (Root)
**POST** `/api/v1/knowledgebase/:kbId/upload`

**Form Data:**
- `kb_id`: string (required)
- `files`: file[] (max 1000) - Append each file separately with key "files"
- `file_paths`: string[] - Append each filename separately with key "file_paths"
- `last_modified`: number[] - Append each timestamp separately with key "last_modified"

**Important Notes:**
- The endpoint uses lowercase `knowledgebase` (not `knowledgeBase`)
- Each file must have corresponding entries in `file_paths` and `last_modified` arrays
- Arrays are created by appending multiple values with the same key name
- Do NOT manually set `Content-Type: multipart/form-data` header - let the HTTP client set it automatically with proper boundary

**Example:**
```bash
POST {{esbackend}}/api/v1/knowledgebase/a77fbf1c-abf2-486d-beac-62459952f001/upload
Content-Type: multipart/form-data
```

**JavaScript/TypeScript Example:**
```typescript
const formData = new FormData();
formData.append('kb_id', knowledgeBaseId);
formData.append('files', file);
formData.append('file_paths', file.name);
formData.append('last_modified', file.lastModified.toString());

// For multiple files:
files.forEach((file) => formData.append('files', file));
files.forEach((file) => formData.append('file_paths', file.name));
files.forEach((file) => formData.append('last_modified', file.lastModified.toString()));

// Let axios/fetch set Content-Type automatically
await apiClient.post(endpoint, formData);
```

#### 15. Upload Records (Folder)
**POST** `/api/v1/knowledgebase/:kbId/folder/:folderId/upload`

**Form Data:**
- `kb_id`: string (required)
- `files`: file[] (max 1000) - Append each file separately with key "files"
- `file_paths`: string[] - Append each filename/path separately with key "file_paths"
- `last_modified`: number[] - Append each timestamp separately with key "last_modified"

**Important Notes:**
- The endpoint uses lowercase `knowledgebase` (not `knowledgeBase`)
- Same array handling as root upload endpoint
- `file_paths` can include relative folder structure (e.g., "subfolder/file.pdf")
- Do NOT manually set `Content-Type: multipart/form-data` header

**Example:**
```bash
POST {{esbackend}}/api/v1/knowledgebase/a77fbf1c-abf2-486d-beac-62459952f001/folder/77d2cc72/upload
Content-Type: multipart/form-data
```

#### 16. Get Record
**GET** `/api/v1/knowledgeBase/:recordId`

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgeBase/cf3d64bd-fc23-443e-a89a-70c5139f6b11
Authorization: Bearer {{authToken}}
```

#### 17. Update Record
**PUT** `/api/v1/knowledgeBase/:recordId`

**Form Data:**
- `file`: file
- `recordName`: string

**Example:**
```bash
PUT {{esbackend}}/api/v1/knowledgeBase/cf3d64bd-fc23-443e-a89a-70c5139f6b11
Authorization: Bearer {{authToken}}
Content-Type: multipart/form-data
```

#### 18. Delete Record
**DELETE** `/api/v1/knowledgeBase/:recordId`

**Example:**
```bash
DELETE {{esbackend}}/api/v1/knowledgeBase/263984c4-104b-4b59-96e8-d20f2f4ecabe
Authorization: Bearer {{authToken}}
```

#### 19. Download Record
**GET** `/api/v1/document/:recordId/download`

**Example:**
```bash
GET {{esbackend}}/api/v1/document/6964abcf41522eb41abd220/download
Authorization: Bearer {{authToken}}
```

#### 20. Stream Record Content
**GET** `/api/v1/knowledgeBase/stream/record/:recordId`

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgeBase/stream/record/8bd9c56a
Authorization: Bearer {{authToken}}
```

#### 21. Reindex Record
**POST** `/api/v1/knowledgeBase/reindex/record/:recordId`

**Example:**
```bash
POST {{esbackend}}/api/v1/knowledgeBase/reindex/record/49f5b846
Authorization: Bearer {{authToken}}
```

#### 22. Create KB Permission
**POST** `/api/v1/knowledgeBase/:kbId/permissions`

**Body:**
```json
{
  "userIds": ["uuid"],
  "teamIds": ["uuid"],
  "role": "READER" | "WRITER" | "ADMIN"
}
```

**Example:**
```bash
POST {{esbackend}}/api/v1/knowledgeBase/a77fbf1c/permissions
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"userIds": ["d4528b13"], "teamIds": ["c4d4edff"], "role": "READER"}
```

#### 23. Get All Permissions
**GET** `/api/v1/knowledgeBase/:kbId/permissions`

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgeBase/a77fbf1c/permissions
Authorization: Bearer {{authToken}}
```

#### 24. Update Permissions
**PUT** `/api/v1/knowledgeBase/:kbId/permissions`

**Body:**
```json
{
  "userIds": ["uuid"],
  "teamIds": ["uuid"],
  "role": "READER" | "WRITER" | "ADMIN"
}
```

**Example:**
```bash
PUT {{esbackend}}/api/v1/knowledgeBase/a77fbf1c/permissions
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"userIds": ["d4528b13"], "role": "WRITER"}
```

#### 25. Remove Permission
**DELETE** `/api/v1/knowledgeBase/:kbId/permissions`

**Body:**
```json
{
  "userIds": ["uuid"],
  "teamIds": ["uuid"]
}
```

**Example:**
```bash
DELETE {{esbackend}}/api/v1/knowledgeBase/a77fbf1c/permissions
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"userIds": [], "teamIds": ["c4d4edff"]}
```

#### 26. Resync Connector
**POST** `/api/v1/knowledgeBase/resync/connector`

**Body:**
```json
{
  "connectorName": "string",
  "connectorId": "uuid"
}
```

**Example:**
```bash
POST {{esbackend}}/api/v1/knowledgeBase/resync/connector
Authorization: Bearer {{authToken}}
Content-Type: application/json

{"connectorName": "Gmail", "connectorId": "a2e31a44"}
```

#### 27. Get Connector Stats
**GET** `/api/v1/knowledgeBase/stats/:connectorId`

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgeBase/stats/a2e31a44
Authorization: Bearer {{authToken}}
```

#### 28. Get KB Limits
**GET** `/api/v1/knowledgebase/limits`

**Example:**
```bash
GET {{esbackend}}/api/v1/knowledgebase/limits
Authorization: Bearer {{authToken}}
```

## Collections View Interaction Flows

### Flow 1: Initial Load (0 State)

**Trigger:** User navigates to Knowledge Base → Collections tab

**Actions:**
1. Call **API 1** (Get Knowledge Hub Nodes) with:
   - `onlyContainers=true` (sidebar requires only folders/groups)
   - `page=1&limit=50`
   - `include=counts`
2. Store first-level nodes in sidebar state
3. Display 0-state in data area (welcome message, icons, instructions)

**Result:** Sidebar shows top-level folders/KBs, data area shows empty state

---

### Flow 2: Sidebar Folder Toggle (Expansion)

**Trigger:** User clicks folder/KB to expand/collapse

**Actions:**
1. **If expanding** and children not loaded:
   - Call **API 1** with `kbIds={folderId}&onlyContainers=true`
   - Store children under parent node in sidebar tree state
   - Mark folder as expanded
2. **If collapsing:**
   - Toggle expanded state to false (keep children cached)

**Result:** Sidebar tree expands/collapses without affecting data area

---

### Flow 3: Folder Selection (Data Area Load)

**Trigger:** User clicks folder/KB name to view contents

**Actions:**
1. Call **API 2** (Get Node Items) with:
   - `nodeType` = type of selected node (kb, folder, recordGroup, etc.)
   - `nodeId` = ID of selected node
   - `page=1&limit=50`
   - `include=counts,permissions,breadcrumbs,availableFilters`
2. Store response in data area state:
   - `items` → table rows
   - `pagination` → pagination controls
   - `breadcrumbs` → breadcrumb navigation
   - `availableFilters` → filter dropdown options
   - `permissions` → action button states
3. Update URL query params: `?nodeType=folder&nodeId=xxx`

**Result:** Data area displays folder contents with full metadata

---

### Flow 4: Filter Application

**Trigger:** User applies filter (record type, date range, search, etc.)

**Actions:**
1. Determine context:
   - **If folder selected:** Call **API 2** with nodeType/nodeId + filter params
   - **If no folder (All Records):** Call **API 1** with filter params
2. Append filter params to API call:
   - Search: `q=query`
   - Record types: `recordTypes=FILE,WEBPAGE`
   - Date: `createdAt=gte:X,lte:Y`
   - Sort: `sortBy=updatedAt&sortOrder=desc`
3. Update data area with filtered results
4. Update URL query params to reflect filters

**Result:** Data area shows filtered items, sidebar unchanged

---

### Flow 5: Pagination

**Trigger:** User clicks next/previous page

**Actions:**
1. Call same API as current view (API 1 or API 2) with:
   - Same filters/search/sort params
   - Updated `page` param
2. Replace data area items with new page
3. Update pagination state

**Result:** Data area shows next/previous page, filters preserved

---

## UI State Management

### Sidebar State
```typescript
{
  nodes: TreeNode[];          // Hierarchical folder structure
  expandedIds: Set<string>;   // Track expanded folders
  selectedId: string | null;  // Currently selected folder
  loading: boolean;
}
```

### Data Area State
```typescript
{
  items: NodeItem[];          // Current page items
  pagination: PaginationMeta;
  breadcrumbs: Breadcrumb[];
  availableFilters: FilterOptions;
  permissions: Permissions;
  currentNode: NodeInfo | null;
  filters: AppliedFilters;
  loading: boolean;
}
```

---

## UI Components Mapping

## UI Components Mapping

### Existing Components
- **Sidebar** → `sidebar.tsx` - Navigation tree (uses **API 1** with `onlyContainers=true` and `kbIds` filter)
- **Header** → `header.tsx` - Breadcrumbs, page title (uses `breadcrumbs` from API responses)
- **DataTable** → `kb-data-table.tsx` - File/folder listing (uses **API 1** or **API 2** based on context)
- **FilterBar** → `filter-bar.tsx` - Search, sort, filter controls (uses `availableFilters` from API responses)
- **SearchBar** → `search-bar.tsx` - Global search input (triggers **API 1** or **API 2** with `q` param)
- **UploadSidebar** → `upload-data-sidebar.tsx` - File upload (uses Action APIs #14, #15)
- **CreateFolderDialog** → `create-folder-dialog.tsx` - Folder creation (uses Action APIs #9, #10)
- **MoveFolderSidebar** → `move-folder-sidebar.tsx` - Move items (needs implementation with Action APIs)
- **ReplaceFileDialog** → `replace-file-dialog.tsx` - Replace file (uses Action API #17)
- **FileIcon** → `file-icon.tsx` - File type icons (display only)
- **FilterDropdown** → `filter-dropdown.tsx` - Multi-select filters (uses `availableFilters` from API responses)
- **DateRangePicker** → `date-range-picker.tsx` - Date filtering (converts to `createdAt`/`updatedAt` params)

---

## Feature Groups

## Feature Groups

### Collections Tab (Get Operations - Knowledge Hub)
- **Root navigation** → **API 1** (all apps/KBs at root level)
- **Hierarchical browsing** → **API 2** (child items with pagination)
- **Sidebar tree** → **API 1** with `onlyContainers=true` and `kbIds` filter for expansion
- **Main content view** → **API 2** with nodeType/nodeId for folder contents
- **Search & filter** → **API 1** or **API 2** with query params (`q`, `nodeTypes`, `recordTypes`, etc.)
- **Sorting** → Both APIs support `sortBy` and `sortOrder`
- **Breadcrumbs** → `include=breadcrumbs` in API calls
- **Item counts** → `include=counts` in API calls
- **Permissions** → `include=permissions` in API calls
- **Available filters** → `include=availableFilters` to populate filter dropdowns

### Legacy KB Tab (Action Operations)
- **KB listing** → Action API #4 (with pagination/search/sort)
- **KB CRUD** → Action APIs #3, #5, #6, #7
- **Folder CRUD** → Action APIs #9, #10, #11, #12, #13
- **File upload** → Action APIs #14, #15
- **Record CRUD** → Action APIs #16, #17, #18
- **Download/stream** → Action APIs #19, #20
- **Reindex** → Action API #21
- **Permission management** → Action APIs #22-25
- **Connector sync** → Action API #26
- **Stats & limits** → Action APIs #27, #28

---
