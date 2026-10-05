from app.connectors.core.constants import AuthFieldKeys, OAuthDefaults
from app.connectors.core.registry.types import AuthField, FieldType

SALESFORCE_LOGIN_URL_PLACEHOLDER = "https://login.salesforce.com"
SALESFORCE_LOGIN_URL_DESCRIPTION = (
    "Where users sign in. Leave blank for a production org. "
    "For a sandbox, enter https://test.salesforce.com or the sandbox's "
    "My Domain URL, such as https://yourcompany--uat.sandbox.my.salesforce.com. "
    "A production org can also use its My Domain URL."
)


def salesforce_login_url_field() -> AuthField:
    """The optional login host on a Salesforce OAuth app.

    The connector and the agent toolset both register an OAuth config named
    "Salesforce", and the later registration replaces the earlier one, so both
    must declare this field or it drops out of the saved OAuth app.
    """
    return AuthField(
        name=AuthFieldKeys.LOGIN_URL,
        display_name="Salesforce Login URL",
        placeholder=SALESFORCE_LOGIN_URL_PLACEHOLDER,
        description=SALESFORCE_LOGIN_URL_DESCRIPTION,
        field_type=FieldType.URL.value,
        required=False,
        usage="CONFIGURE",
        max_length=OAuthDefaults.MAX_URL_LENGTH,
    )
