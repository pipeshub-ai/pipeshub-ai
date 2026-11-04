import base64

INVALID_ACCOUNT_KEY = (
    "The AccountKey in the connection string is not a valid storage account key. Copy the connection "
    "string again from the storage account's Access keys page in the Azure portal."
)

# A key or SAS that is well formed but that Azure does not accept, told apart from one that is malformed.
CREDENTIALS_REFUSED = (
    "Azure refused the credentials: they are wrong, have expired or were revoked. For a connection "
    "string, copy it again from the storage account's Access keys (or Shared access signature) page "
    "in the Azure portal; for Entra ID, check the app registration's tenant, client ID and secret."
)

# Covers both sign-in methods: a connection string names the host through AccountName and
# EndpointSuffix, Entra ID through the Blob Endpoint URL.
ACCOUNT_NOT_FOUND = (
    "The storage account's host name does not exist. Check the AccountName and EndpointSuffix "
    "in the connection string, or the Blob Endpoint for Entra ID."
)


def account_key_problem(connection_string: str, client_cls: type) -> str | None:
    """INVALID_ACCOUNT_KEY when the SDK would sign requests with a key it cannot decode, else None.

    The SDK accepts any AccountKey and only fails on the first request, with base64's
    "Incorrect padding". Parsing with ``client_cls`` keeps its rules: keys are case-insensitive,
    and an AccountKey without an AccountName is not used (a SAS beside it is used instead).
    """
    try:
        credential = client_cls.from_connection_string(connection_string).credential
    except ValueError:
        # A malformed connection string fails where the client is built, with the SDK's own message.
        return None
    key = getattr(credential, "account_key", None)
    if key is None:
        return None
    try:
        # Not validate=True: the SDK decodes leniently, so a key it can sign with must pass here.
        return None if base64.b64decode(key) else INVALID_ACCOUNT_KEY
    except ValueError:
        # binascii.Error for bad base64, a plain ValueError for a non-ASCII key.
        return INVALID_ACCOUNT_KEY
