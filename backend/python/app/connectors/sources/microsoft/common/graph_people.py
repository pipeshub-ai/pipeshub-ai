from msgraph.generated.models.identity_set import IdentitySet

from app.models.entities import SourcePerson

# SharePoint's built-in account; Graph names it with no id or email.
_SHAREPOINT_SYSTEM_ACCOUNT = "system account"


def source_person(identity_set: IdentitySet | None) -> SourcePerson | None:
    """The person a Graph identitySet names, keyed by Entra object id and email.

    A user wins over an application (a user acting through an app is still the
    user); an application with no user is a service account.
    """
    if not isinstance(identity_set, IdentitySet):
        return None
    user, app = identity_set.user, identity_set.application
    if user is not None:
        email = (user.additional_data or {}).get("email") or None
        is_system = (user.display_name or "").strip().casefold() == _SHAREPOINT_SYSTEM_ACCOUNT
        if not (user.id or email or is_system):
            return None
        return SourcePerson(
            source_id=user.id, email=email, display_name=user.display_name, is_service_account=is_system
        )
    if app is not None and app.id:
        return SourcePerson(source_id=app.id, display_name=app.display_name, is_service_account=True)
    return None
