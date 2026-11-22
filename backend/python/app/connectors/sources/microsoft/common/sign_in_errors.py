"""What a failed Microsoft sign-in (AADSTS code) means, in words that say what to fix."""

import re

_RESOURCE_NOT_FOUND = "AADSTS500011"

# Microsoft sign-in (AADSTS) codes of a failed token request, and what to fix.
_SIGN_IN_PROBLEMS: tuple[tuple[str, str], ...] = (
    ("AADSTS7000215", "the client secret is wrong. Copy the secret's Value (not its Secret ID) "
                      "from the app registration and save it again."),
    ("AADSTS7000222", "the client secret has expired. Create a new client secret in the app "
                      "registration and save it here."),
    ("AADSTS700016", "no app with this Application (Client) ID exists in this directory. Check the "
                     "Client ID and the Directory (Tenant) ID."),
    ("AADSTS90002", "the Directory (Tenant) ID was not found. Check it in the app registration."),
    ("AADSTS900023", "the Directory (Tenant) ID is not valid. Check it in the app registration."),
    ("AADSTS700027", "the certificate is not registered on the app, or it does not match the private key. Upload "
                     "the public certificate (.cer, .pem or .crt) of this private key under Certificates & secrets "
                     "in the app registration."),
    ("AADSTS700024", "the client assertion is outside its valid time window. Check that the certificate has not "
                     "expired, and ask your PipesHub administrator to check the server's clock."),
    (_RESOURCE_NOT_FOUND, "the resource the connector asked for was not found in this directory, or the app has "
                          "no consent for it. Check the Directory (Tenant) ID, and grant admin consent for the "
                          "app's API permissions in the app registration."),
    ("AADSTS65001", "an admin has not granted consent to the app. Grant admin consent for its API "
                    "permissions in the app registration."),
    ("AADSTS700082", "the sign-in has expired. Sign in again on the connector's Authorize tab."),
    ("AADSTS70008", "the sign-in has expired. Sign in again on the connector's Authorize tab."),
)


def known_sign_in_problem(error: object, resource_setting: str | None = None) -> str | None:
    """What to fix, only when ``error`` carries one of the AADSTS codes listed above.

    ``resource_setting`` names the connector setting the failed token request was for (for example
    "SharePoint domain"); without it, an unknown resource is blamed on the tenant or on consent.
    """
    text = str(error)
    for code, problem in _SIGN_IN_PROBLEMS:
        if re.search(rf"\b{code}\b", text):
            if code == _RESOURCE_NOT_FOUND and resource_setting:
                return (f"Microsoft sign-in failed ({code}): the resource the connector asked for was not found in "
                        f"this directory. Check the {resource_setting} in the connector's settings and the "
                        f"Directory (Tenant) ID.")
            return f"Microsoft sign-in failed ({code}): {problem}"
    return None


def describe_sign_in_error(error: object) -> str | None:
    """A message that says what to fix, when ``error`` is any Microsoft sign-in (AADSTS) failure."""
    known = known_sign_in_problem(error)
    if known:
        return known
    other = re.search(r"\bAADSTS\d+\b", str(error))
    if other:
        return f"Microsoft sign-in failed ({other.group(0)}). Check the connector's credentials, or sign in again."
    return None
