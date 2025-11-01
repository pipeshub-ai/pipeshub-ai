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
    GOOGLE_MAIL = "GMAIL"
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

    DROPBOX = "DROPBOX"
    WEB = "WEB"
    BOOKSTACK = "BOOKSTACK"

    SERVICENOW = "SERVICENOW"

    UNKNOWN = "UNKNOWN"

class AppGroups(Enum):
    GOOGLE_WORKSPACE = "Google Workspace"
    NOTION = "Notion"
    ATLASSIAN = "Atlassian"
    MICROSOFT = "Microsoft"
    DROPBOX = "Dropbox"
    SERVICENOW = "Servicenow"
    WEB = "Web"
    BOOKSTACK = "BookStack"

class OriginTypes(Enum):
    CONNECTOR = "CONNECTOR"
    UPLOAD = "UPLOAD"

class LegacyCollectionNames(Enum):
    KNOWLEDGE_BASE = "knowledgeBase"
    PERMISSIONS_TO_KNOWLEDGE_BASE = "permissionsToKnowledgeBase"
    BELONGS_TO_KNOWLEDGE_BASE = "belongsToKnowledgeBase"
    BELONGS_TO_KB = "belongsToKB"

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
    PERMISSIONS_TO_KB = "permissionsToKB"

    # Drive related
    DRIVES = "drives"
    USER_DRIVE_RELATION = "userDriveRelation"

    # Record types
    FILES = "files"
    LINKS = "links"
    MAILS = "mails"
    #MESSAGES = "messages"
    WEBPAGES = "webpages"
    TICKETS = "tickets"

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

    # Permissions
    PERMISSIONS = "permissions"

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

class ProgressStatus(Enum):
    NOT_STARTED = "NOT_STARTED"
    PAUSED = "PAUSED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"

class RecordTypes(Enum):
    FILE = "FILE"
    ATTACHMENT = "ATTACHMENT"
    LINK = "LINK"
    MAIL = "MAIL"
    DRIVE = "DRIVE"
    WEBPAGE = "WEBPAGE"
    TICKET = "TICKET"
    MESSAGE = "MESSAGE"
    WEBPAGE_COMMENT = "WEBPAGE_COMMENT"
    NOTION_DATABASE = "NOTION_DATABASE"
    NOTION_PAGE = "NOTION_PAGE"
    SHAREPOINT_LIST = "SHAREPOINT_LIST"
    SHAREPOINT_PAGE = "SHAREPOINT_PAGE"

class RecordRelations(Enum):
    PARENT_CHILD = "PARENT_CHILD"
    SIBLING = "SIBLING"
    ATTACHMENT = "ATTACHMENT"

class EventTypes(Enum):
    NEW_RECORD = "newRecord"
    UPDATE_RECORD = "updateRecord"
    DELETE_RECORD = "deleteRecord"
    REINDEX_RECORD = "reindexRecord"
    REINDEX_FAILED = "reindexFailed"

class AccountType(Enum):
    INDIVIDUAL = "individual"
    ENTERPRISE = "enterprise"
    BUSINESS = "business"
    ADMIN = "admin"
