import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.utils.time_conversion import get_epoch_timestamp_in_ms


class HealthStatus(Enum):
    """Health check status enumeration"""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class ScopeCategory(Enum):
    """Slack API scope categories"""
    ESSENTIAL = "ESSENTIAL"  # Must have for basic functionality
    RECOMMENDED = "RECOMMENDED"  # Should have for full functionality
    OPTIONAL = "OPTIONAL"  # Nice to have for enhanced features


@dataclass
class ScopeRequirement:
    """Represents a required Slack scope with metadata"""
    scope: str
    category: ScopeCategory
    description: str
    required_for: List[str]  # Features that require this scope


@dataclass
class HealthCheckResult:
    """Represents the result of a health check"""
    status: HealthStatus
    timestamp: int
    checks_passed: List[str]
    checks_failed: List[str]
    warnings: List[str]
    details: Dict[str, Any]
    bot_info: Optional[Dict[str, Any]] = None
    team_info: Optional[Dict[str, Any]] = None
    scopes: Optional[List[str]] = None
    missing_scopes: Optional[List[str]] = None


class SlackHealthChecker:
    """Focused Slack health checker for bot credentials and scopes"""
    # Define required scopes for connector functionality
    REQUIRED_SCOPES = [
    ScopeRequirement(
        scope="channels:read",
        category=ScopeCategory.ESSENTIAL,
        description="Read public channel information",
        required_for=["channel_sync", "workspace_discovery"]
    ),
    ScopeRequirement(
        scope="channels:history",
        category=ScopeCategory.ESSENTIAL,
        description="Read message history from public channels",
        required_for=["message_sync", "historical_data"]
    ),
    ScopeRequirement(
        scope="channels:join",
        category=ScopeCategory.RECOMMENDED,
        description="Join public channels",
        required_for=["auto_channel_joining", "expanded_access"]
    ),
    ScopeRequirement(
            scope="groups:history",
            category=ScopeCategory.ESSENTIAL,
            description="Read message history from private channels",
            required_for=["private_message_sync", "message.groups events"]
    ),
    ScopeRequirement(
        scope="groups:read",
        category=ScopeCategory.ESSENTIAL,
        description="Read private channel information",
        required_for=["private_channel_sync"]
    ),
    ScopeRequirement(
        scope="im:read",
        category=ScopeCategory.ESSENTIAL,
        description="Read direct message information",
        required_for=["dm_sync"]
    ),
    ScopeRequirement(
        scope="mpim:read",
        category=ScopeCategory.ESSENTIAL,
        description="Read multiparty direct message information",
        required_for=["group_dm_sync"]
    ),
    ScopeRequirement(
        scope="users:read",
        category=ScopeCategory.ESSENTIAL,
        description="Read user information",
        required_for=["user_sync", "message_attribution"]
    ),
    ScopeRequirement(
        scope="team:read",
        category=ScopeCategory.ESSENTIAL,
        description="Read workspace/team information",
        required_for=["workspace_sync", "team_info"]
    ),
    ScopeRequirement(
        scope="files:read",
        category=ScopeCategory.ESSENTIAL,
        description="Read file information and metadata",
        required_for=["file_sync", "attachment_processing"]
    ),

    # Recommended scopes - important for full functionality
    ScopeRequirement(
        scope="usergroups:read",
        category=ScopeCategory.RECOMMENDED,
        description="Read user group information",
        required_for=["user_group_sync", "permissions"]
    ),

    # Optional scopes - nice to have for enhanced features
    ScopeRequirement(
        scope="emoji:read",
        category=ScopeCategory.OPTIONAL,
        description="Read custom emoji information",
        required_for=["emoji_sync"]
    )
    ]

    def __init__(self, logger, config_service, arango_service=None) -> None:
        """Initialize the Slack health checker"""
        self.logger = logger
        self.config_service = config_service
        self.arango_service = arango_service  # Can be None

        # Create scope lookup dictionaries
        self.essential_scopes = {
            req.scope for req in self.REQUIRED_SCOPES
            if req.category == ScopeCategory.ESSENTIAL
        }
        self.recommended_scopes = {
            req.scope for req in self.REQUIRED_SCOPES
            if req.category == ScopeCategory.RECOMMENDED
        }
        self.optional_scopes = {
            req.scope for req in self.REQUIRED_SCOPES
            if req.category == ScopeCategory.OPTIONAL
        }

        self.all_scope_requirements = {req.scope: req for req in self.REQUIRED_SCOPES}

    async def validate_credentials_before_storage(
        self,
        bot_token: str,
        signing_secret: str,
        strict_mode: bool = True
    ) -> HealthCheckResult:
        """Validate Slack credentials before storing them in the system"""
        try:
            self.logger.info("🔍 Starting slack credential validation ")
            start_time = time.time()

            result = HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                timestamp=get_epoch_timestamp_in_ms(),
                checks_passed=[],
                checks_failed=[],
                warnings=[],
                details={
                    "validation_type": "pre_storage",
                    "strict_mode": strict_mode
                }
            )

            # 1. Basic token format validation
            if not await self._validate_token_format(bot_token, result):
                result.status = HealthStatus.UNHEALTHY
                return result

            # 2. Basic signing secret validation
            if not await self._validate_signing_secret_format(signing_secret, result):
                result.status = HealthStatus.UNHEALTHY
                return result

            # 3. Create Slack client and test connectivity
            client = AsyncWebClient(token=bot_token)

            # 4. Test basic auth and get bot info
            if not await self._test_auth_and_get_bot_info(client, result):
                result.status = HealthStatus.UNHEALTHY
                return result

            # 5. Get and validate team info
            if not await self._validate_team_access(client, result):
                result.status = HealthStatus.UNHEALTHY
                return result

            # 6. Validate scopes by checking auth.test response and infer from API calls
            scope_validation = await self._validate_scopes(client, result, strict_mode)

            # 7. Determine final health status
            result.status = self._determine_health_status(result, scope_validation, strict_mode)

            # 8. Log results
            duration = time.time() - start_time
            result.details["validation_duration_seconds"] = round(duration, 2)

            self._log_validation_results(result, duration)

            return result

        except Exception as e:
            self.logger.error(f"❌ Credential validation failed: {str(e)}")
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                timestamp=get_epoch_timestamp_in_ms(),
                checks_passed=[],
                checks_failed=[f"validation_exception: {str(e)}"],
                warnings=[],
                details={"error": str(e), "validation_type": "pre_storage"}
            )

    async def perform_ongoing_health_check(
        self,
        org_id: str,
        user_id: Optional[str] = None
    ) -> HealthCheckResult:
        """Perform ongoing health check for existing credentials"""
        try:
            self.logger.info(f"🔍 Starting ongoing health check for org: {org_id}")

            # Get existing credentials
            from app.connectors.sources.slack.core.slack_token_handler import (
                SlackTokenHandler,
            )
            token_handler = SlackTokenHandler(self.logger, self.config_service)

            try:
                creds = await token_handler.get_bot_config(org_id=org_id, user_id=user_id)
                if not creds:
                    return HealthCheckResult(
                        status=HealthStatus.UNHEALTHY,
                        timestamp=get_epoch_timestamp_in_ms(),
                        checks_passed=[],
                        checks_failed=["credentials_not_found"],
                        warnings=[],
                        details={"error": "No credentials found for organization"}
                    )
            except Exception as e:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    timestamp=get_epoch_timestamp_in_ms(),
                    checks_passed=[],
                    checks_failed=[f"credential_fetch_failed: {str(e)}"],
                    warnings=[],
                    details={"error": str(e)}
                )

            # Perform validation with existing credentials
            return await self.validate_credentials_before_storage(
                bot_token=creds["bot_token"],
                signing_secret=creds["signing_secret"],
                org_id=org_id,
                user_id=user_id,
                strict_mode=False  # Be more lenient for ongoing checks
            )

        except Exception as e:
            self.logger.error(f"❌ Ongoing health check failed: {str(e)}")
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                timestamp=get_epoch_timestamp_in_ms(),
                checks_passed=[],
                checks_failed=[f"health_check_exception: {str(e)}"],
                warnings=[],
                details={"error": str(e), "check_type": "ongoing"}
            )

    async def _validate_token_format(self, bot_token: str, result: HealthCheckResult) -> bool:
        """Validate basic token format"""
        try:
            MIN_BOT_TOKEN_LENGTH = 50
            if not bot_token:
                result.checks_failed.append("empty_bot_token")
                return False

            if not bot_token.startswith('xoxb-'):
                result.checks_failed.append("invalid_bot_token_format")
                result.warnings.append("Bot token should start with 'xoxb-'")
                return False

            if len(bot_token) < MIN_BOT_TOKEN_LENGTH:  # Slack tokens are typically much longer
                result.checks_failed.append("bot_token_too_short")
                result.warnings.append("Bot token appears to be too short")
                return False

            result.checks_passed.append("token_format_valid")
            return True

        except Exception as e:
            result.checks_failed.append(f"token_format_validation_error: {str(e)}")
            return False

    async def _validate_signing_secret_format(self, signing_secret: str, result: HealthCheckResult) -> bool:
        """Validate basic signing secret format"""
        try:
            MIN_SIGNING_SECRET_LENGTH = 32
            if not signing_secret:
                result.checks_failed.append("empty_signing_secret")
                return False

            if len(signing_secret) < MIN_SIGNING_SECRET_LENGTH:  # Slack signing secrets are typically 32+ characters
                result.checks_failed.append("signing_secret_too_short")
                result.warnings.append("Signing secret appears to be too short")
                return False

            result.checks_passed.append("signing_secret_format_valid")
            return True

        except Exception as e:
            result.checks_failed.append(f"signing_secret_validation_error: {str(e)}")
            return False

    async def _test_auth_and_get_bot_info(self, client: AsyncWebClient, result: HealthCheckResult) -> bool:
        """Test authentication and get bot information"""
        try:
            self.logger.debug("Testing auth.test endpoint...")

            response = await client.auth_test()

            if not response.get("ok"):
                error = response.get("error", "unknown_error")
                result.checks_failed.append(f"auth_test_failed: {error}")

                # Provide specific error guidance
                if error == "invalid_auth":
                    result.warnings.append("Bot token is invalid or revoked")
                elif error == "account_inactive":
                    result.warnings.append("Slack workspace is inactive")
                elif error == "token_revoked":
                    result.warnings.append("Bot token has been revoked")
                else:
                    result.warnings.append(f"Authentication failed: {error}")

                return False

            # Extract bot and team information
            bot_info = {
                "bot_id": response.get("bot_id"),
                "user_id": response.get("user_id"),
                "team_id": response.get("team_id"),
                "team": response.get("team"),
                "url": response.get("url"),
                "user": response.get("user")
            }

            result.bot_info = bot_info
            result.checks_passed.append("auth_test_passed")
            result.details["bot_info"] = bot_info

            self.logger.info(f"✅ Bot authenticated successfully: {bot_info.get('user')}")
            return True

        except SlackApiError as e:
            result.checks_failed.append(f"slack_api_error: {e.response['error']}")
            result.warnings.append(f"Slack API error: {e.response['error']}")
            return False
        except Exception as e:
            result.checks_failed.append(f"auth_test_exception: {str(e)}")
            return False

    async def _validate_team_access(self, client: AsyncWebClient, result: HealthCheckResult) -> bool:
        """Validate team/workspace access"""
        try:
            self.logger.debug("Testing team.info endpoint...")

            response = await client.team_info()

            if not response.get("ok"):
                error = response.get("error", "unknown_error")
                result.checks_failed.append(f"team_info_failed: {error}")
                result.warnings.append(f"Cannot access team information: {error}")
                return False

            team_info = response.get("team", {})
            result.team_info = team_info
            result.checks_passed.append("team_access_validated")
            result.details["team_info"] = {
                "id": team_info.get("id"),
                "name": team_info.get("name"),
                "domain": team_info.get("domain"),
                "email_domain": team_info.get("email_domain")
            }

            self.logger.info(f"✅ Team access validated: {team_info.get('name')}")
            return True

        except SlackApiError as e:
            result.checks_failed.append(f"team_info_api_error: {e.response['error']}")
            return False
        except Exception as e:
            result.checks_failed.append(f"team_info_exception: {str(e)}")
            return False

    async def _validate_scopes(self, client: AsyncWebClient, result: HealthCheckResult, strict_mode: bool) -> Dict[str, Any]:
        """
        Validate required scopes by reading the 'x-oauth-scopes' response header
        on an auth.test call, instead of inferring via multiple API requests.
        """
        try:
            self.logger.debug("Validating bot scopes via headers…")

            # Make a low-overhead call to auth.test to get scope headers
            slack_resp = await client.api_call("auth.test")
            if not slack_resp.get("ok"):
                result.checks_failed.append("scope_validation_auth_failed")
                return {"has_required_scopes": False, "scope_issues": True}

            # Read scopes from headers
            scopes_header = slack_resp.headers.get("x-oauth-scopes", "")
            bot_scopes = {s.strip() for s in scopes_header.split(",") if s.strip()}
            result.scopes = sorted(bot_scopes)

            # Optional: see which scopes this method itself checked for
            accepted_header = slack_resp.headers.get("x-accepted-oauth-scopes", "")
            result.details["accepted_oauth_scopes"] = [
                s.strip() for s in accepted_header.split(",") if s.strip()
            ]

            # Determine missing scopes
            missing_essential   = self.essential_scopes   - bot_scopes
            missing_recommended = self.recommended_scopes - bot_scopes
            missing_optional    = self.optional_scopes    - bot_scopes
            result.missing_scopes = sorted(
                missing_essential | missing_recommended | missing_optional
            )

            # Report essentials
            if missing_essential:
                result.checks_failed.append(
                    f"missing_essential_scopes: {sorted(missing_essential)}"
                )
                for scope in missing_essential:
                    req = self.all_scope_requirements[scope]
                    result.warnings.append(
                        f"Missing essential '{scope}': {req.description}. "
                        f"Required for {', '.join(req.required_for)}"
                    )
            else:
                result.checks_passed.append("essential_scopes_present")

            # Report recommended
            if missing_recommended:
                msg = f"missing_recommended_scopes: {sorted(missing_recommended)}"
                if strict_mode:
                    result.checks_failed.append(msg)
                else:
                    result.warnings.append(msg)
                for scope in missing_recommended:
                    req = self.all_scope_requirements[scope]
                    result.warnings.append(
                        f"Missing recommended '{scope}': {req.description}. "
                        f"Required for {', '.join(req.required_for)}"
                    )
            else:
                result.checks_passed.append("recommended_scopes_present")

            # Report optional
            if missing_optional:
                result.warnings.append(
                    f"missing_optional_scopes: {sorted(missing_optional)}"
                )

            # Populate detailed summary
            result.details["scope_analysis"] = {
                "total_detected": len(bot_scopes),
                "detected": result.scopes,
                "missing_essential": sorted(missing_essential),
                "missing_recommended": sorted(missing_recommended),
                "missing_optional": sorted(missing_optional),
            }

            has_required = not missing_essential
            scope_issues = missing_essential or (strict_mode and missing_recommended)
            return {
                "has_required_scopes": has_required,
                "scope_issues": bool(scope_issues),
                "missing_essential": missing_essential,
                "missing_recommended": missing_recommended
            }

        except Exception as e:
            result.checks_failed.append(f"scope_validation_exception: {e}")
            return {"has_required_scopes": False, "scope_issues": True}

    async def _infer_scopes_from_api_calls(self, client: AsyncWebClient) -> set:
        """Infer available scopes by testing key API endpoints"""
        inferred_scopes = set()

        # Test basic endpoints to infer scopes
        test_endpoints = [
            ("conversations_list", {"types": "public_channel", "limit": 1}, ["channels:read"]),
            ("conversations_list", {"types": "private_channel", "limit": 1}, ["groups:read"]),
            ("conversations_list", {"types": "im", "limit": 1}, ["im:read"]),
            ("conversations_list", {"types": "mpim", "limit": 1}, ["mpim:read"]),
            ("users_list", {"limit": 1}, ["users:read"]),
            ("team_info", {}, ["team:read"]),
            ("files_list", {"count": 1}, ["files:read"]),
            ("usergroups_list", {}, ["usergroups:read"]),
            ("emoji_list", {}, ["emoji:read"]),
        ]

        for method_name, params, associated_scopes in test_endpoints:
            try:
                # Use the client's API call method
                method = getattr(client, method_name, None)
                if method:
                    response = await method(**params)
                    if response.get("ok"):
                        inferred_scopes.update(associated_scopes)
                await asyncio.sleep(0.1)  # Small delay to avoid rate limits
            except Exception as e:
                self.logger.debug(f"General error during {method_name}: {str(e)}")

        return inferred_scopes

    def _determine_health_status(self, result: HealthCheckResult, scope_validation: Dict[str, Any], strict_mode: bool) -> HealthStatus:
        """Determine overall health status based on check results"""
        # If any critical checks failed, status is UNHEALTHY
        critical_failures = [
            check for check in result.checks_failed
            if any(keyword in check for keyword in [
                "auth_test_failed", "team_info_failed", "token_format", "signing_secret",
                "missing_essential_scopes"
            ])
        ]

        if critical_failures:
            return HealthStatus.UNHEALTHY

        # If strict mode and missing recommended scopes, status is DEGRADED
        if strict_mode and scope_validation.get("missing_recommended"):
            return HealthStatus.DEGRADED

        # If there are warnings but no critical failures, status is DEGRADED
        if result.warnings:
            return HealthStatus.DEGRADED

        # If all essential checks passed, status is HEALTHY
        if result.checks_passed and scope_validation.get("has_required_scopes", False):
            return HealthStatus.HEALTHY

        return HealthStatus.UNKNOWN

    def _log_validation_results(self, result: HealthCheckResult, duration: float) -> None:
        """Log the validation results with appropriate level"""
        status_emoji = {
            HealthStatus.HEALTHY: "✅",
            HealthStatus.DEGRADED: "⚠️",
            HealthStatus.UNHEALTHY: "❌",
            HealthStatus.UNKNOWN: "❓"
        }

        emoji = status_emoji.get(result.status, "❓")

        self.logger.info(
            f"{emoji} Slack credential validation completed: {result.status.value} "
            f"(Duration: {duration:.2f}s)"
        )

        if result.checks_passed:
            self.logger.info(f"✅ Passed checks: {', '.join(result.checks_passed)}")

        if result.checks_failed:
            self.logger.error(f"❌ Failed checks: {', '.join(result.checks_failed)}")

        if result.warnings:
            self.logger.warning(f"⚠️ Warnings: {', '.join(result.warnings)}")

        # Log scope summary
        if result.scopes:
            self.logger.info(f"🔑 Detected scopes ({len(result.scopes)}): {', '.join(sorted(result.scopes))}")

        if result.missing_scopes:
            self.logger.warning(f"🔑 Missing scopes ({len(result.missing_scopes)}): {', '.join(sorted(result.missing_scopes))}")

    async def _store_health_check_result(self, result: HealthCheckResult, org_id: str) -> None:
        """Store health check result in ArangoDB for tracking (optional)"""
        try:
            if self.arango_service:  # Only store if ArangoDB is available
                health_record = {
                    "org_id": org_id,
                    "timestamp": result.timestamp,
                    "status": result.status.value,
                    "checks_passed": result.checks_passed,
                    "checks_failed": result.checks_failed,
                    "warnings": result.warnings,
                    "details": result.details,
                    "bot_info": result.bot_info,
                    "team_info": result.team_info,
                    "scopes": result.scopes,
                    "missing_scopes": result.missing_scopes
                }

                # Store in a health check collection
                await self.arango_service.insert_document("slack_health_checks", health_record)
            else:
                self.logger.debug("ArangoDB not available - skipping health check result storage")

        except Exception as e:
            self.logger.error(f"❌ Failed to store health check result: {str(e)}")

    def get_scope_requirements_summary(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get a summary of all scope requirements organized by category"""
        summary = {
            "essential": [],
            "recommended": [],
            "optional": []
        }

        for req in self.REQUIRED_SCOPES:
            scope_info = {
                "scope": req.scope,
                "description": req.description,
                "required_for": req.required_for
            }

            if req.category == ScopeCategory.ESSENTIAL:
                summary["essential"].append(scope_info)
            elif req.category == ScopeCategory.RECOMMENDED:
                summary["recommended"].append(scope_info)
            else:
                summary["optional"].append(scope_info)

        return summary


# Integration helper function for use in credential setup
async def validate_slack_credentials(
    bot_token: str,
    signing_secret: str,
    logger,
    config_service,
    arango_service=None,
    strict_mode: bool = True
) -> HealthCheckResult:
    """Convenience function for validating Slack credentials"""
    health_checker = SlackHealthChecker(
        logger=logger,
        config_service=config_service,
        arango_service=arango_service
    )

    return await health_checker.validate_credentials_before_storage(
        bot_token=bot_token,
        signing_secret=signing_secret,
        strict_mode=strict_mode
    )
