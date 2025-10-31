from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field  #type:ignore


class ZammadTicket(BaseModel):
    """Pydantic model for Zammad ticket API response"""

    # Core ticket fields
    id: int = Field(description="Unique ticket ID")
    number: str = Field(description="Ticket number")
    title: str = Field(description="Ticket title")

    # ID references
    group_id: int = Field(description="Group ID")
    priority_id: int = Field(description="Priority ID")
    state_id: int = Field(description="State ID")
    organization_id: Optional[int] = Field(default=None, description="Organization ID")
    owner_id: int = Field(description="Owner ID")
    customer_id: int = Field(description="Customer ID")

    # Optional text fields
    note: Optional[str] = Field(default=None, description="Ticket note")
    type: Optional[str] = Field(default=None, description="Ticket type")

    # Response time tracking
    first_response_at: Optional[datetime] = Field(default=None, description="First response timestamp")
    first_response_escalation_at: Optional[datetime] = Field(default=None, description="First response escalation timestamp")
    first_response_in_min: Optional[int] = Field(default=None, description="First response time in minutes")
    first_response_diff_in_min: Optional[int] = Field(default=None, description="First response time difference in minutes")

    # Close time tracking
    close_at: Optional[datetime] = Field(default=None, description="Close timestamp")
    close_escalation_at: Optional[datetime] = Field(default=None, description="Close escalation timestamp")
    close_in_min: Optional[int] = Field(default=None, description="Close time in minutes")
    close_diff_in_min: Optional[int] = Field(default=None, description="Close time difference in minutes")
    last_close_at: Optional[datetime] = Field(default=None, description="Last close timestamp")

    # Update time tracking
    update_escalation_at: Optional[datetime] = Field(default=None, description="Update escalation timestamp")
    update_in_min: Optional[int] = Field(default=None, description="Update time in minutes")
    update_diff_in_min: Optional[int] = Field(default=None, description="Update time difference in minutes")

    # Contact tracking
    last_contact_at: Optional[datetime] = Field(default=None, description="Last contact timestamp")
    last_contact_agent_at: Optional[datetime] = Field(default=None, description="Last agent contact timestamp")
    last_contact_customer_at: Optional[datetime] = Field(default=None, description="Last customer contact timestamp")
    last_owner_update_at: Optional[datetime] = Field(default=None, description="Last owner update timestamp")

    # Article creation info
    create_article_type_id: Optional[int] = Field(default=None, description="Article type ID for creation")
    create_article_sender_id: Optional[int] = Field(default=None, description="Article sender ID for creation")
    article_count: int = Field(default=0, description="Number of articles")

    # Escalation and pending
    escalation_at: Optional[datetime] = Field(default=None, description="Escalation timestamp")
    pending_time: Optional[datetime] = Field(default=None, description="Pending time")
    time_unit: Optional[float] = Field(default=None, description="Time unit")

    # Additional fields
    preferences: Dict[str, Any] = Field(default_factory=dict, description="Ticket preferences")

    # User references
    updated_by_id: int = Field(description="ID of user who last updated the ticket")
    created_by_id: int = Field(description="ID of user who created the ticket")

    # Timestamps
    created_at: datetime = Field(description="Ticket creation timestamp")
    updated_at: datetime = Field(description="Ticket update timestamp")

    # Checklist references
    checklist_id: Optional[int] = Field(default=None, description="Checklist ID")
    referencing_checklist_ids: List[int] = Field(default_factory=list, description="List of referencing checklist IDs")
    referencing_checklists: List[Any] = Field(default_factory=list, description="List of referencing checklists")

    # Article references
    article_ids: List[int] = Field(default_factory=list, description="List of article IDs")

    # Time accounting
    ticket_time_accounting_ids: List[int] = Field(default_factory=list, description="List of time accounting IDs")
    ticket_time_accounting: List[Any] = Field(default_factory=list, description="List of time accounting entries")

    # Expanded fields (string representations)
    group: Optional[str] = Field(default=None, description="Group name")
    organization: Optional[str] = Field(default=None, description="Organization name")
    state: Optional[str] = Field(default=None, description="State name")
    priority: Optional[str] = Field(default=None, description="Priority name")
    owner: Optional[str] = Field(default=None, description="Owner name")
    customer: Optional[str] = Field(default=None, description="Customer email/name")
    created_by: Optional[str] = Field(default=None, description="Created by email/name")
    updated_by: Optional[str] = Field(default=None, description="Updated by email/name")
    create_article_type: Optional[str] = Field(default=None, description="Article type name for creation")
    create_article_sender: Optional[str] = Field(default=None, description="Article sender name for creation")

    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ZammadUser(BaseModel):
    """Pydantic model for Zammad user API response"""

    # Core user fields
    id: int = Field(description="Unique user ID")
    login: str = Field(description="User login name")
    firstname: str = Field(default="", description="First name")
    lastname: str = Field(default="", description="Last name")
    email: str = Field(default="", description="Email address")

    # Organization
    organization_id: Optional[int] = Field(default=None, description="Organization ID")

    # Profile fields
    image: Optional[str] = Field(default=None, description="User image")
    image_source: Optional[str] = Field(default=None, description="Image source")
    web: str = Field(default="", description="Website URL")
    phone: str = Field(default="", description="Phone number")
    fax: str = Field(default="", description="Fax number")
    mobile: str = Field(default="", description="Mobile number")
    department: str = Field(default="", description="Department")

    # Address fields
    street: str = Field(default="", description="Street address")
    zip: str = Field(default="", description="ZIP/Postal code")
    city: str = Field(default="", description="City")
    country: str = Field(default="", description="Country")
    address: str = Field(default="", description="Full address")

    # Status fields
    vip: bool = Field(default=False, description="VIP status")
    verified: bool = Field(default=False, description="Verified status")
    active: bool = Field(default=False, description="Active status")

    # Additional info
    note: str = Field(default="", description="User notes")
    last_login: Optional[datetime] = Field(default=None, description="Last login timestamp")
    source: Optional[str] = Field(default=None, description="User source")
    login_failed: int = Field(default=0, description="Number of failed login attempts")

    # Out of office
    out_of_office: bool = Field(default=False, description="Out of office status")
    out_of_office_start_at: Optional[datetime] = Field(default=None, description="Out of office start timestamp")
    out_of_office_end_at: Optional[datetime] = Field(default=None, description="Out of office end timestamp")
    out_of_office_replacement_id: Optional[int] = Field(default=None, description="Out of office replacement user ID")

    # Metadata
    preferences: Dict[str, Any] = Field(default_factory=dict, description="User preferences")

    # User references
    updated_by_id: int = Field(description="ID of user who last updated this user")
    created_by_id: int = Field(description="ID of user who created this user")

    # Timestamps
    created_at: datetime = Field(description="User creation timestamp")
    updated_at: datetime = Field(description="User update timestamp")

    # ID lists
    role_ids: List[int] = Field(default_factory=list, description="List of role IDs")
    two_factor_preference_ids: List[int] = Field(default_factory=list, description="List of two-factor preference IDs")
    organization_ids: List[int] = Field(default_factory=list, description="List of organization IDs")
    authorization_ids: List[int] = Field(default_factory=list, description="List of authorization IDs")
    overview_sorting_ids: List[int] = Field(default_factory=list, description="List of overview sorting IDs")
    group_ids: Dict[str, Any] = Field(default_factory=dict, description="Dictionary of group IDs")

    # Expanded fields (full objects)
    roles: List[Any] = Field(default_factory=list, description="List of role objects")
    two_factor_preferences: List[Any] = Field(default_factory=list, description="List of two-factor preference objects")
    organizations: List[Any] = Field(default_factory=list, description="List of organization objects")
    authorizations: List[Any] = Field(default_factory=list, description="List of authorization objects")
    overview_sortings: List[Any] = Field(default_factory=list, description="List of overview sorting objects")
    groups: Dict[str, Any] = Field(default_factory=dict, description="Dictionary of group objects")
    created_by: Optional[str] = Field(default=None, description="Created by user name")
    updated_by: Optional[str] = Field(default=None, description="Updated by user name")

    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ZammadOrganization(BaseModel):
    """Pydantic model for Zammad organization API response"""

    # Core organization fields
    id: int = Field(description="Unique organization ID")
    name: str = Field(description="Organization name")

    # Configuration fields
    shared: bool = Field(default=True, description="Shared organization status")
    domain: str = Field(default="", description="Organization domain")
    domain_assignment: bool = Field(default=False, description="Domain assignment status")
    active: bool = Field(default=True, description="Active status")
    vip: bool = Field(default=False, description="VIP status")
    note: str = Field(default="", description="Organization notes")

    # User references
    updated_by_id: int = Field(description="ID of user who last updated this organization")
    created_by_id: int = Field(description="ID of user who created this organization")

    # Timestamps
    created_at: datetime = Field(description="Organization creation timestamp")
    updated_at: datetime = Field(description="Organization update timestamp")

    # Member lists
    member_ids: List[int] = Field(default_factory=list, description="List of primary member user IDs")
    secondary_member_ids: List[int] = Field(default_factory=list, description="List of secondary member user IDs")

    # Expanded fields
    members: List[str] = Field(default_factory=list, description="List of member emails/names")
    created_by: Optional[str] = Field(default=None, description="Created by user name")
    updated_by: Optional[str] = Field(default=None, description="Updated by user name")

    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ZammadRole(BaseModel):
    """Pydantic model for Zammad role API response"""

    # Core role fields
    id: int = Field(description="Unique role ID")
    name: str = Field(description="Role name")

    # Configuration fields
    preferences: Dict[str, Any] = Field(default_factory=dict, description="Role preferences")
    default_at_signup: bool = Field(default=False, description="Default role at signup")
    active: bool = Field(default=True, description="Active status")
    note: str = Field(default="", description="Role notes/description")

    # User references
    updated_by_id: int = Field(description="ID of user who last updated this role")
    created_by_id: int = Field(description="ID of user who created this role")

    # Timestamps
    created_at: datetime = Field(description="Role creation timestamp")
    updated_at: datetime = Field(description="Role update timestamp")

    # Permission and group IDs
    permission_ids: List[int] = Field(default_factory=list, description="List of permission IDs")
    knowledge_base_permission_ids: List[int] = Field(default_factory=list, description="List of knowledge base permission IDs")
    group_ids: Dict[str, Any] = Field(default_factory=dict, description="Dictionary of group IDs")

    # Expanded fields
    created_by: Optional[str] = Field(default=None, description="Created by user name")
    updated_by: Optional[str] = Field(default=None, description="Updated by user name")
    permissions: List[str] = Field(default_factory=list, description="List of permission names")
    knowledge_base_permissions: List[str] = Field(default_factory=list, description="List of knowledge base permission names")
    groups: Dict[str, Any] = Field(default_factory=dict, description="Dictionary of group objects")
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ZammadKnowledgeBase(BaseModel):
    """Pydantic model for Zammad knowledge base API response"""

    # Core KB fields
    id: int = Field(description="Unique knowledge base ID")
    active: bool = Field(default=True, description="Active status")
    title: str = Field(default="", description="Knowledge base title")

    # Appearance fields
    iconset: str = Field(default="FontAwesome", description="Icon set to use")
    color_highlight: str = Field(default="#38ae6a", description="Highlight color")
    color_header: str = Field(default="#f9fafb", description="Header color")
    color_header_link: str = Field(default="hsl(206,8%,50%)", description="Header link color")

    # Layout fields
    homepage_layout: str = Field(default="grid", description="Homepage layout type")
    category_layout: str = Field(default="grid", description="Category layout type")
    show_feed_icon: bool = Field(default=True, description="Show feed icon")

    # Custom address
    custom_address: Optional[str] = Field(default=None, description="Custom address/URL")

    # Timestamps
    created_at: datetime = Field(description="Knowledge base creation timestamp")
    updated_at: datetime = Field(description="Knowledge base update timestamp")

    # ID lists
    translation_ids: List[int] = Field(default_factory=list, description="List of translation IDs")
    kb_locale_ids: List[int] = Field(default_factory=list, description="List of KB locale IDs")
    category_ids: List[int] = Field(default_factory=list, description="List of category IDs")
    answer_ids: List[int] = Field(default_factory=list, description="List of answer IDs")
    permission_ids: List[int] = Field(default_factory=list, description="List of permission IDs")

    # Expanded fields
    permissions_effective: List[Any] = Field(default_factory=list, description="List of effective permissions")
    answers: List[Any] = Field(default_factory=list, description="List of answer objects")
    permissions: List[Any] = Field(default_factory=list, description="List of permission objects")
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ZammadGroup(BaseModel):
    """Pydantic model for Zammad group API response"""

    # Core group fields
    id: int = Field(description="Unique group ID")
    name: str = Field(description="Group name")
    name_last: str = Field(default="", description="Last name of group")

    # Configuration fields
    signature_id: Optional[int] = Field(default=None, description="Signature ID")
    email_address_id: Optional[int] = Field(default=None, description="Email address ID")
    parent_id: Optional[int] = Field(default=None, description="Parent group ID")
    assignment_timeout: Optional[int] = Field(default=None, description="Assignment timeout in minutes")
    follow_up_possible: str = Field(default="yes", description="Follow-up possible setting")
    reopen_time_in_days: Optional[int] = Field(default=None, description="Reopen time in days")
    follow_up_assignment: bool = Field(default=True, description="Follow-up assignment enabled")
    active: bool = Field(default=True, description="Active status")
    shared_drafts: bool = Field(default=True, description="Shared drafts enabled")
    note: str = Field(default="", description="Group notes/description")

    # User references
    updated_by_id: int = Field(description="ID of user who last updated this group")
    created_by_id: int = Field(description="ID of user who created this group")

    # Timestamps
    created_at: datetime = Field(description="Group creation timestamp")
    updated_at: datetime = Field(description="Group update timestamp")

    # User list
    user_ids: List[int] = Field(default_factory=list, description="List of user IDs in this group")

    # Expanded fields
    created_by: Optional[str] = Field(default=None, description="Created by user name")
    updated_by: Optional[str] = Field(default=None, description="Updated by user name")
    email_address: Optional[str] = Field(default=None, description="Email address")
    signature: Optional[str] = Field(default=None, description="Signature name")
    users: List[str] = Field(default_factory=list, description="List of user emails/names")

    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ZammadKBCategory(BaseModel):
    """Pydantic model for Zammad Knowledge Base Category"""

    # Core category fields
    id: int = Field(description="Unique category ID")
    knowledge_base_id: int = Field(description="Parent knowledge base ID")
    parent_id: Optional[int] = Field(default=None, description="Parent category ID")
    title: str = Field(default="", description="Category title")
    
    # Display fields
    category_icon: Optional[str] = Field(default=None, description="Category icon")
    position: int = Field(default=0, description="Position in list")
    
    # Timestamps
    created_at: datetime = Field(description="Category creation timestamp")
    updated_at: datetime = Field(description="Category update timestamp")
    
    # User references (optional - not always returned by API)
    updated_by_id: Optional[int] = Field(default=None, description="ID of user who last updated this category")
    created_by_id: Optional[int] = Field(default=None, description="ID of user who created this category")
    
    # Related IDs
    translation_ids: List[int] = Field(default_factory=list, description="List of translation IDs")
    answer_ids: List[int] = Field(default_factory=list, description="List of answer IDs")
    
    # Permission IDs
    permission_ids: List[int] = Field(default_factory=list, description="List of permission IDs")
    
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ZammadKBAnswer(BaseModel):
    """Pydantic model for Zammad Knowledge Base Answer (Article)"""

    # Core answer fields
    id: int = Field(description="Unique answer ID")
    category_id: int = Field(description="Parent category ID")
    title: str = Field(default="", description="Answer title")
    
    # Content fields
    promoted: bool = Field(default=False, description="Promoted/featured status")
    internal_note: Optional[str] = Field(default=None, description="Internal note")
    internal_at: Optional[datetime] = Field(default=None, description="Internal timestamp")
    archived_at: Optional[datetime] = Field(default=None, description="Archive timestamp")
    
    # Position
    position: int = Field(default=0, description="Position in list")
    
    # Timestamps
    created_at: datetime = Field(description="Answer creation timestamp")
    updated_at: datetime = Field(description="Answer update timestamp")
    published_at: Optional[datetime] = Field(default=None, description="Publish timestamp")
    
    # User references (optional - not always returned by API)
    updated_by_id: Optional[int] = Field(default=None, description="ID of user who last updated this answer")
    created_by_id: Optional[int] = Field(default=None, description="ID of user who created this answer")
    
    # Related IDs
    translation_ids: List[int] = Field(default_factory=list, description="List of translation IDs")
    attachment_ids: List[int] = Field(default_factory=list, description="List of attachment IDs")
    
    # Translations (expanded)
    translations: List[Dict[str, Any]] = Field(default_factory=list, description="List of translation objects with title and content")
    
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }

