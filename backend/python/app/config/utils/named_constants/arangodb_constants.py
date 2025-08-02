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
    KNOWLEDGE_BASE = "KB"

class AppGroups(Enum):
    GOOGLE_WORKSPACE = "Google Workspace"

class RecordTypes(Enum):
    FILE = "FILE"
    ATTACHMENT = "ATTACHMENT"
    LINK = "LINK"
    MAIL = "MAIL"
    DRIVE = "DRIVE"


class RecordRelations(Enum):
    PARENT_CHILD = "PARENT_CHILD"
    SIBLING = "SIBLING"
    ATTACHMENT = "ATTACHMENT"


class OriginTypes(Enum):
    CONNECTOR = "CONNECTOR"
    UPLOAD = "UPLOAD"


class EventTypes(Enum):
    NEW_RECORD = "newRecord"
    UPDATE_RECORD = "updateRecord"
    DELETE_RECORD = "deleteRecord"
    REINDEX_RECORD = "reindexRecord"
    REINDEX_FAILED = "reindexFailed"


class CollectionNames(Enum):
    # Records and Record relations
    RECORDS = "records"
    RECORD_RELATIONS = "recordRelations"
    RECORD_GROUPS = "recordGroups"

    # Knowledge base
    KNOWLEDGE_BASE = "knowledgeBase"
    IS_OF_TYPE = "isOfType"
    BELONGS_TO_KNOWLEDGE_BASE = "belongsToKnowledgeBase"
    PERMISSIONS_TO_KNOWLEDGE_BASE = "permissionsToKnowledgeBase"
    BELONGS_TO_KB = "belongsToKB"
    PERMISSIONS_TO_KB = "permissionsToKB"

    # Drive related
    DRIVES = "drives"
    USER_DRIVE_RELATION = "userDriveRelation"

    # Record types
    FILES = "files"
    ATTACHMENTS = "attachments"
    LINKS = "links"
    MAILS = "mails"

    # Users and groups
    PEOPLE = "people"
    USERS = "users"
    GROUPS = "groups"
    ORGS = "organizations"
    ANYONE = "anyone"
    BELONGS_TO = "belongsTo"

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

    # Graphs
    FILE_ACCESS_GRAPH = "fileAccessGraph"
    KNOWLEDGE_GRAPH = "knowledgeGraph"
    
    APPS = "apps"
    ORG_APP_RELATION = "orgAppRelation"
    USER_APP_RELATION = "userAppRelation"
    ORG_DEPARTMENT_RELATION = "orgDepartmentRelation"

    BLOCKS = "blocks"


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


class MimeTypes(Enum):
    PDF = "application/pdf"
    GMAIL = "text/gmail_content"
    GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
    GOOGLE_DOCS = "application/vnd.google-apps.document"
    GOOGLE_SHEETS = "application/vnd.google-apps.spreadsheet"
    GOOGLE_DRIVE_FOLDER = "application/vnd.google-apps.folder"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    DOC = "application/msword"
    PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    PPT = "application/vnd.ms-powerpoint"
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    XLS = "application/vnd.ms-excel"
    CSV = "text/csv"


class ProgressStatus(Enum):
    NOT_STARTED = "NOT_STARTED"
    PAUSED = "PAUSED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"


class AccountType(Enum):
    INDIVIDUAL = "individual"
    ENTERPRISE = "enterprise"
    BUSINESS = "business"
    ADMIN = "admin"
