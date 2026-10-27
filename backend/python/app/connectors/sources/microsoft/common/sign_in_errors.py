"""What a failed Microsoft sign-in (AADSTS code) means, in words that say what to fix."""

import re

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
    ("AADSTS700027", "the certificate is not registered on the app. Upload the public certificate (.cer, .pem "
                     "or .crt) under Certificates & secrets in the app registration."),
    ("AADSTS700024", "the client assertion is outside its valid time window. Check that the certificate has not "
                     "expired, and ask your PipesHub administrator to check the server's clock."),
    ("AADSTS65001", "an admin has not granted consent to the app. Grant admin consent for its API "
                    "permissions in the app registration."),
    ("AADSTS700082", "the sign-in has expired. Sign in again on the connector's Authorize tab."),
    ("AADSTS70008", "the sign-in has expired. Sign in again on the connector's Authorize tab."),
)


def known_sign_in_problem(error: object) -> str | None:
    """What to fix, only when ``error`` carries one of the AADSTS codes listed above."""
    text = str(error)
    for code, problem in _SIGN_IN_PROBLEMS:
        if re.search(rf"\b{code}\b", text):
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
