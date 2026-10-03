"""Route dependency that lets only organisation admins through."""

from fastapi import HTTPException, Request

from app.config.constants.http_status_code import HttpStatusCode


async def require_admin_caller(request: Request) -> None:
    # Imported here: app.edition_config pulls in route modules, which import this one.
    from app.edition_config import check_user_is_admin

    user = getattr(request.state, "user", None) or {}
    config_service = request.app.container.config_service()
    if not await check_user_is_admin(user.get("userId", ""), user.get("orgId"), request, config_service):
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can do this."
        )
