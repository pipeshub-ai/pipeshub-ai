from enum import Enum


class DepartmentNames(Enum):
    LEGAL = "Legal"
    COMPLIANCE_RISK = "Compliance/Risk Management"
    IT_SECURITY = "IT & Security"
    PRODUCT_MANAGEMENT = "Product Management"
    SALES = "Sales"
    ENGINEERING = "Engineering/Technology"
    HR = "Human Resources"
    PROCUREMENT = "Procurement"
    FINANCE = "Finance"
    OPERATIONS = "Operations"
    RND = "Research and Development"
    EXECUTIVE = "Executive Leadership"
    QA = "Quality Assurance"
    DEVOPS = "Devops/Site Reliability Engineering"
    LEGAL_PATENT = "Legal/Patent Management"
    FACILITIES_ADMIN = "Facilities / Administration"
    DATA_ANALYTICS = "Data Analytics / Insights"
    BUSINESS_DEV = "Business Development / Partnerships"
    ESG = "Environmental, Social, and Governance"
    TRAINING = "Training and Enablement"
    MARKETING = "Marketing"
    INVESTOR_RELATIONS = "Investor Relations"
    CUSTOMER_SUCCESS = "Customer Success"
    OTHERS = "Others"


class Connectors(Enum):
    GOOGLE_DRIVE = "DRIVE"
    GOOGLE_DRIVE_WORKSPACE = "DRIVE WORKSPACE"
    GOOGLE_MAIL = "GMAIL"
    GOOGLE_MAIL_WORKSPACE = "GMAIL WORKSPACE"
    GOOGLE_CALENDAR = "CALENDAR"

    ONEDRIVE = "ONEDRIVE"
    SHAREPOINT_ONLINE = "SHAREPOINT ONLINE"
    OUTLOOK = "OUTLOOK"
    OUTLOOK_CALENDAR = "OUTLOOK CALENDAR"
    MICROSOFT_TEAMS = "MICROSOFT TEAMS"

    NOTION = "NOTION"
    SLACK = "SLACK"

    KNOWLEDGE_BASE = "KB"

    CONFLUENCE = "CONFLUENCE"
    JIRA = "JIRA"
    BOX = "BOX"
    NEXTCLOUD = "NEXTCLOUD"
    DROPBOX = "DROPBOX"
    DROPBOX_PERSONAL = "DROPBOX PERSONAL"
    WEB = "WEB"
    BOOKSTACK = "BOOKSTACK"

    SERVICENOW = "SERVICENOW"
    S3 = "S3"
    MINIO = "MINIO"
    GCS = "GCS"
    AZURE_BLOB = "AZURE BLOB"
    AZURE_FILES = "AZURE FILES"
    LINEAR = "LINEAR"
    ZAMMAD = "ZAMMAD"

    SNOWFLAKE = "SNOWFLAKE"
    POSTGRESQL = "POSTGRESQL"

    UNKNOWN = "UNKNOWN"

    RSS = "RSS"


class AppGroups(Enum):
    GOOGLE_WORKSPACE = "Google Workspace"
    NOTION = "Notion"
    ATLASSIAN = "Atlassian"
    MICROSOFT = "Microsoft"
    DROPBOX = "Dropbox"
    BOX = "Box"
    SERVICENOW = "Servicenow"
    NEXTCLOUD = "Nextcloud"
    WEB = "Web"
    BOOKSTACK = "BookStack"
    S3 = "S3"
    MINIO = "MinIO"
    GOOGLE_CLOUD = "Google Cloud"
    AZURE = "Azure"
    LINEAR = "Linear"
    ZAMMAD = "Zammad"
    LOCAL_STORAGE = "Local Storage"
    RSS = "RSS"

    SNOWFLAKE = "Snowflake"
    POSTGRESQL = "PostgreSQL"  

class OriginTypes(Enum):
    CONNECTOR = "CONNECTOR"
    UPLOAD = "UPLOAD"

class LegacyCollectionNames(Enum):
    KNOWLEDGE_BASE = "knowledgeBase"
    PERMISSIONS_TO_KNOWLEDGE_BASE = "permissionsToKnowledgeBase"
    BELONGS_TO_KNOWLEDGE_BASE = "belongsToKnowledgeBase"
    BELONGS_TO_KB = "belongsToKB"
    PERMISSIONS = "permissions"
    PERMISSIONS_TO_KB = "permissionsToKB"

class LegacyGraphNames(Enum):
    FILE_ACCESS_GRAPH = "fileAccessGraph"

class GraphNames(Enum):
    KNOWLEDGE_GRAPH = "knowledgeGraph"

class CollectionNames(Enum):
    # Records and Record relations
    RECORDS = "records"
    RECORD_RELATIONS = "recordRelations"
    RECORD_GROUPS = "recordGroups"
    SYNC_POINTS = "syncPoints"
    INHERIT_PERMISSIONS = "inheritPermissions"

    # Knowledge base
    IS_OF_TYPE = "isOfType"
    PERMISSION = "permission"

    # Drive related
    DRIVES = "drives"
    USER_DRIVE_RELATION = "userDriveRelation"

    # Record types
    FILES = "files"
    LINKS = "links"
    MAILS = "mails"
    #MESSAGES = "messages"
    WEBPAGES = "webpages"
    COMMENTS = "comments"
    TICKETS = "tickets"
    ENTITY_RELATIONS = "entityRelations"
    PROJECTS = "projects"
    SQL_TABLES = "sqlTables"
    SQL_VIEWS = "sqlViews"

    # Users and groups
    PEOPLE = "people"
    USERS = "users"
    GROUPS = "groups"
    ROLES = "roles"
    ORGS = "organizations"
    # DOMAINS = "domains"
    ANYONE = "anyone"
    # ANYONE_WITH_LINK = "anyoneWithLink"
    BELONGS_TO = "belongsTo"
    TEAMS = "teams"

    # Departments
    DEPARTMENTS = "departments"
    BELONGS_TO_DEPARTMENT = "belongsToDepartment"
    CATEGORIES = "categories"
    BELONGS_TO_CATEGORY = "belongsToCategory"
    LANGUAGES = "languages"
    BELONGS_TO_LANGUAGE = "belongsToLanguage"
    TOPICS = "topics"
    BELONGS_TO_TOPIC = "belongsToTopic"
    SUBCATEGORIES1 = "subcategories1"
    SUBCATEGORIES2 = "subcategories2"
    SUBCATEGORIES3 = "subcategories3"
    INTER_CATEGORY_RELATIONS = "interCategoryRelations"


    # Other
    CHANNEL_HISTORY = "channelHistory"
    PAGE_TOKENS = "pageTokens"

    APPS = "apps"
    ORG_APP_RELATION = "orgAppRelation"
    USER_APP_RELATION = "userAppRelation"
    ORG_DEPARTMENT_RELATION = "orgDepartmentRelation"

    BLOCKS = "blocks"

    # WEBPAGE_RECORD="webpageRecord"
    # WEBPAGE_COMMENT_RECORD="webpageCommentRecord"

    # NOTION_DATABASE_RECORD="notionDatabaseRecord"
    BELONGS_TO_RECORD_GROUP="belongsToRecordGroup"

    # Storage mappings
    VIRTUAL_RECORD_TO_DOC_ID_MAPPING = "virtualRecordToDocIdMapping"
    # Agent Builder collections
    AGENT_TEMPLATES = "agentTemplates"
    AGENT_INSTANCES = "agentInstances"

    # Agent Builder Graph collections
    AGENT_KNOWLEDGE = "agentKnowledge"
    AGENT_TOOLSETS = "agentToolsets"
    AGENT_TOOLS = "agentTools"

    # Agent Builder Graph edges
    AGENT_HAS_KNOWLEDGE = "agentHasKnowledge"
    AGENT_HAS_TOOLSET = "agentHasToolset"
    TOOLSET_HAS_TOOL = "toolsetHasTool"

class QdrantCollectionNames(Enum):
    RECORDS = "records"


class ExtensionTypes(Enum):
    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    PPTX = "pptx"
    PPT = "ppt"
    XLSX = "xlsx"
    XLS = "xls"
    CSV = "csv"
    TSV = "tsv"
    TXT = "txt"
    MD = "md"
    MDX = "mdx"
    HTML = "html"
    PNG = "png"
    JPG = "jpg"
    JPEG = "jpeg"
    WEBP = "webp"
    SVG = "svg"
    HEIC = "heic"
    HEIF = "heif"
    SQL_TABLE = "sql_table"  
    SQL_VIEW = "sql_view"    


class MimeTypes(Enum):
    PDF = "application/pdf"
    GMAIL = "text/gmail_content"
    GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
    GOOGLE_DOCS = "application/vnd.google-apps.document"
    GOOGLE_SHEETS = "application/vnd.google-apps.spreadsheet"
    GOOGLE_DRIVE_FOLDER = "application/vnd.google-apps.folder"
    FOLDER = "text/directory"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    DOC = "application/msword"
    PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    PPT = "application/vnd.ms-powerpoint"
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    XLS = "application/vnd.ms-excel"
    CSV = "text/csv"
    TSV = "text/tab-separated-values"
    BIN = "application/octet-stream"
    NOTION_TEXT = "notion/text"
    NOTION_PAGE_COMMENT_TEXT = "notion/pageCommentText"
    HTML = "text/html"
    PLAIN_TEXT = "text/plain"
    MARKDOWN = "text/markdown"
    MDX = "text/mdx"
    GENESIS_32X_ROM = "application/x-genesis-32x-rom"
    JSON = "application/json"
    BLOCKS = "application/blocks"
    XML = "application/xml"
    YAML = "application/yaml"
    UNKNOWN = "application/unknown"
    PNG = "image/png"
    JPG = "image/jpg"
    JPEG = "image/jpeg"
    WEBP = "image/webp"
    SVG = "image/svg+xml"
    HEIC = "image/heic"
    HEIF = "image/heif"
    TEXT = "text/plain"
    ZIP = "application/zip"
    GIF = "image/gif"
    SQL_TABLE = "application/vnd.sql.table"  
    SQL_VIEW = "application/vnd.sql.view"  

RECONCILIATION_ENABLED_MIME_TYPES = {
    MimeTypes.SQL_TABLE.value,
    MimeTypes.SQL_VIEW.value,
    MimeTypes.GOOGLE_DOCS.value,
    MimeTypes.GOOGLE_SHEETS.value,
    MimeTypes.GOOGLE_SLIDES.value,
    MimeTypes.PDF.value,
    MimeTypes.DOCX.value,
    MimeTypes.DOC.value,
    MimeTypes.TEXT.value,
    MimeTypes.HTML.value,
}

class ProgressStatus(Enum):
    NOT_STARTED = "NOT_STARTED"
    PAUSED = "PAUSED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"
    EMPTY = "EMPTY"
    ENABLE_MULTIMODAL_MODELS = "ENABLE_MULTIMODAL_MODELS"
    QUEUED = "QUEUED"
    CONNECTOR_DISABLED = "CONNECTOR_DISABLED"


class RecordTypes(Enum):
    FILE = "FILE"
    ATTACHMENT = "ATTACHMENT"
    LINK = "LINK"
    MAIL = "MAIL"
    GROUP_MAIL = "GROUP_MAIL"
    DRIVE = "DRIVE"
    WEBPAGE = "WEBPAGE"
    DATABASE = "DATABASE"
    DATASOURCE = "DATASOURCE"
    COMMENT = "COMMENT"
    TICKET = "TICKET"
    MESSAGE = "MESSAGE"
    WEBPAGE_COMMENT = "WEBPAGE_COMMENT"
    SHAREPOINT_LIST = "SHAREPOINT_LIST"
    SHAREPOINT_PAGE = "SHAREPOINT_PAGE"

class RecordRelations(Enum):
    PARENT_CHILD = "PARENT_CHILD"
    SIBLING = "SIBLING"
    ATTACHMENT = "ATTACHMENT"
    OTHERS = "OTHERS"
    LINKED_TO = "LINKED_TO"
    BLOCKS = "BLOCKS"
    DUPLICATES = "DUPLICATES"
    DEPENDS_ON = "DEPENDS_ON"
    CLONES = "CLONES"
    IMPLEMENTS = "IMPLEMENTS"
    REVIEWS = "REVIEWS"
    CAUSES = "CAUSES"
    RELATED = "RELATED"
    FOREIGN_KEY = "FOREIGN_KEY"


class EntityRelations(Enum):
    """Standard edge types for entity relationships"""
    ASSIGNED_TO = "ASSIGNED_TO"
    REPORTED_BY = "REPORTED_BY"
    CREATED_BY = "CREATED_BY"
    LEAD_BY = "LEAD_BY"

class EventTypes(Enum):
    NEW_RECORD = "newRecord"
    UPDATE_RECORD = "updateRecord"
    DELETE_RECORD = "deleteRecord"
    REINDEX_RECORD = "reindexRecord"
    REINDEX_FAILED = "reindexFailed"
    BULK_DELETE_RECORDS = "bulkDeleteRecords"

class AccountType(Enum):
    INDIVIDUAL = "individual"
    ENTERPRISE = "enterprise"
    BUSINESS = "business"
    ADMIN = "admin"

class ConnectorScopes(Enum):
    PERSONAL = "personal"
    TEAM = "team"


RECONCILIATION_ENABLED_EXTENSIONS = {
    ExtensionTypes.SQL_TABLE.value,
    ExtensionTypes.SQL_VIEW.value,
    ExtensionTypes.PDF.value,
    ExtensionTypes.DOCX.value,
    ExtensionTypes.DOC.value,
    ExtensionTypes.TXT.value,
    ExtensionTypes.MD.value,
    ExtensionTypes.MDX.value,
    ExtensionTypes.HTML.value
}


RECORD_TYPE_COLLECTION_MAPPING = {
    "FILE": CollectionNames.FILES.value,
    "MAIL": CollectionNames.MAILS.value,
    "GROUP_MAIL": CollectionNames.MAILS.value,
    "WEBPAGE": CollectionNames.WEBPAGES.value,
    "SHAREPOINT_PAGE": CollectionNames.WEBPAGES.value,
    "CONFLUENCE_PAGE": CollectionNames.WEBPAGES.value,
    "CONFLUENCE_BLOGPOST": CollectionNames.WEBPAGES.value,
    "TICKET": CollectionNames.TICKETS.value,
    "COMMENT": CollectionNames.COMMENTS.value,
    "INLINE_COMMENT": CollectionNames.COMMENTS.value,
    "LINK": CollectionNames.LINKS.value,
    "PROJECT": CollectionNames.PROJECTS.value,
    "DATABASE": CollectionNames.WEBPAGES.value,
    "DATASOURCE": CollectionNames.WEBPAGES.value,
    "SQL_TABLE": CollectionNames.SQL_TABLES.value,
    "SQL_VIEW": CollectionNames.SQL_VIEWS.value,
}
