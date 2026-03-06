import json
import logging
from datetime import date, timedelta
from typing import List, Optional

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Input Schemas
# ============================================================================

class GetExclusionDatesInput(BaseModel):
    start_date: str = Field(
        description="Start of the date range (YYYY-MM-DD). Dates before this are excluded."
    )
    end_date: str = Field(
        description="End of the date range (YYYY-MM-DD). Dates after this are excluded."
    )
    holiday_dates: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of company holiday dates (YYYY-MM-DD) to include as exclusions. "
            "These are combined with weekends. Duplicates are handled automatically."
        ),
    )
    include_saturdays: bool = Field(
        default=True,
        description="Whether to include Saturdays in the exclusion list.",
    )
    include_sundays: bool = Field(
        default=True,
        description="Whether to include Sundays in the exclusion list.",
    )


class ListWeekendDatesInput(BaseModel):
    start_date: str = Field(
        description="Start of the date range (YYYY-MM-DD)."
    )
    end_date: str = Field(
        description="End of the date range (YYYY-MM-DD)."
    )


class ParseHolidayDatesInput(BaseModel):
    text: str = Field(
        description=(
            "Raw text content from a Confluence page or other source containing holiday dates. "
            "The tool will extract all dates it can find in any common format."
        ),
    )
    year: Optional[int] = Field(
        default=None,
        description="If provided, only return holidays for this year.",
    )


# ============================================================================
# DateCalculator Toolset
# ============================================================================

@ToolsetBuilder("DateCalculator")\
    .in_group("Internal Tools")\
    .with_description(
        "Date calculation utility — computes weekends, holidays, and exclusion "
        "date lists for recurring event management. Always available, no auth required."
    )\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([AuthBuilder.type("NONE").fields([])])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/calendar.svg"))\
    .build_decorator()
class DateCalculator:
    """Deterministic date computation tool for agents.

    Solves the problem of LLMs being unreliable at enumerating large date
    ranges. The agent calls these tools instead of computing dates itself.
    """

    def __init__(self) -> None:
        logger.info("🚀 Initializing DateCalculator tool")

    # ────────────────────────────────────────────────────────────────────────
    # Tool 1: Get all exclusion dates (weekends + holidays) for a range
    # ────────────────────────────────────────────────────────────────────────

    @tool(
        app_name="date_calculator",
        tool_name="get_exclusion_dates",
        description=(
            "Compute the COMPLETE list of dates to exclude (weekends and/or holidays) "
            "within a date range. Returns a deduplicated, sorted list of YYYY-MM-DD strings "
            "ready to pass directly to delete_recurring_event_occurrence."
        ),
        args_schema=GetExclusionDatesInput,
        when_to_use=[
            "Before calling delete_recurring_event_occurrence to remove weekends and holidays",
            "When extending a recurring event and need to compute dates to skip",
            "When creating a recurring event and need to know which occurrences to delete",
            "Whenever you need a list of Saturdays, Sundays, and/or holidays in a date range",
        ],
        when_not_to_use=[
            "When you already have the exact list of dates to delete",
        ],
        primary_intent=ToolIntent.QUESTION,
        typical_queries=[
            "Get all weekends and holidays between March 6 and July 1, 2026",
            "List exclusion dates for the range 2026-03-06 to 2026-07-01 with holidays on 2026-03-26 and 2026-03-31",
            "Compute dates to skip for a recurring event from today to end of year",
        ],
        llm_description=(
            "Computes the complete, deduplicated list of weekend dates (Saturdays and Sundays) "
            "and optionally company holiday dates within a given range. Returns the list in "
            "YYYY-MM-DD format, sorted, ready to pass to delete_recurring_event_occurrence. "
            "Use this instead of computing dates manually — it is deterministic and never misses a date."
        ),
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
    )
    def get_exclusion_dates(
        self,
        start_date: str,
        end_date: str,
        holiday_dates: Optional[List[str]] = None,
        include_saturdays: bool = True,
        include_sundays: bool = True,
    ) -> str:
        """Compute all exclusion dates in the given range."""
        try:
            d_start = date.fromisoformat(start_date)
            d_end = date.fromisoformat(end_date)

            if d_start > d_end:
                return json.dumps({"error": f"start_date ({start_date}) is after end_date ({end_date})"})

            exclusion_set: set[date] = set()

            # Enumerate weekends
            saturdays = []
            sundays = []
            current = d_start
            while current <= d_end:
                if current.weekday() == 5 and include_saturdays:  # Saturday
                    exclusion_set.add(current)
                    saturdays.append(current.isoformat())
                elif current.weekday() == 6 and include_sundays:  # Sunday
                    exclusion_set.add(current)
                    sundays.append(current.isoformat())
                current += timedelta(days=1)

            # Add holidays (filter to range + deduplicate with weekends)
            holidays_in_range = []
            holidays_on_weekends = []
            holidays_on_weekdays = []

            if holiday_dates:
                for h_str in holiday_dates:
                    try:
                        h_date = date.fromisoformat(h_str.strip())
                        if d_start <= h_date <= d_end:
                            holidays_in_range.append(h_date.isoformat())
                            if h_date in exclusion_set:
                                holidays_on_weekends.append(h_date.isoformat())
                            else:
                                holidays_on_weekdays.append(h_date.isoformat())
                                exclusion_set.add(h_date)
                    except ValueError:
                        logger.warning(f"Skipping invalid holiday date: {h_str}")

            # Sort final list
            sorted_dates = sorted(d.isoformat() for d in exclusion_set)

            return json.dumps({
                "exclusion_dates": sorted_dates,
                "total_count": len(sorted_dates),
                "breakdown": {
                    "saturdays": len(saturdays),
                    "sundays": len(sundays),
                    "holidays_in_range": len(holidays_in_range),
                    "holidays_on_weekdays": len(holidays_on_weekdays),
                    "holidays_on_weekends_deduplicated": len(holidays_on_weekends),
                },
                "detail": {
                    "saturday_dates": saturdays,
                    "sunday_dates": sundays,
                    "holiday_dates_in_range": holidays_in_range,
                    "holidays_added_as_weekday_exclusions": holidays_on_weekdays,
                    "holidays_already_on_weekends": holidays_on_weekends,
                },
                "range": {
                    "start": start_date,
                    "end": end_date,
                    "total_days": (d_end - d_start).days + 1,
                },
            })

        except ValueError as e:
            return json.dumps({"error": f"Invalid date format: {e}. Use YYYY-MM-DD."})
        except Exception as e:
            return json.dumps({"error": f"Unexpected error: {e}"})

    # ────────────────────────────────────────────────────────────────────────
    # Tool 2: List just the weekend dates (no holidays)
    # ────────────────────────────────────────────────────────────────────────

    @tool(
        app_name="date_calculator",
        tool_name="list_weekend_dates",
        description=(
            "List all Saturday and Sunday dates within a date range. "
            "Returns sorted YYYY-MM-DD strings."
        ),
        args_schema=ListWeekendDatesInput,
        when_to_use=[
            "When you only need weekend dates without holidays",
            "When no holiday calendar is available and you need weekends only",
        ],
        when_not_to_use=[
            "When you also need holidays — use get_exclusion_dates instead",
        ],
        primary_intent=ToolIntent.QUESTION,
        typical_queries=[
            "List all weekends between March and June 2026",
            "What are the Saturday and Sunday dates from 2026-03-06 to 2026-04-30",
        ],
        llm_description=(
            "Returns all Saturday and Sunday dates in YYYY-MM-DD format for a given range. "
            "Use get_exclusion_dates instead if you also have holidays to include."
        ),
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
    )
    def list_weekend_dates(self, start_date: str, end_date: str) -> str:
        """List all weekend dates in the range."""
        try:
            d_start = date.fromisoformat(start_date)
            d_end = date.fromisoformat(end_date)

            if d_start > d_end:
                return json.dumps({"error": f"start_date ({start_date}) is after end_date ({end_date})"})

            saturdays = []
            sundays = []
            current = d_start
            while current <= d_end:
                if current.weekday() == 5:
                    saturdays.append(current.isoformat())
                elif current.weekday() == 6:
                    sundays.append(current.isoformat())
                current += timedelta(days=1)

            all_weekends = sorted(saturdays + sundays)

            return json.dumps({
                "weekend_dates": all_weekends,
                "total_count": len(all_weekends),
                "saturdays": len(saturdays),
                "sundays": len(sundays),
                "range": {"start": start_date, "end": end_date},
            })

        except ValueError as e:
            return json.dumps({"error": f"Invalid date format: {e}. Use YYYY-MM-DD."})
        except Exception as e:
            return json.dumps({"error": f"Unexpected error: {e}"})

    # ────────────────────────────────────────────────────────────────────────
    # Tool 3: Parse holiday dates from raw text
    # ────────────────────────────────────────────────────────────────────────

    @tool(
        app_name="date_calculator",
        tool_name="parse_holiday_dates",
        description=(
            "Extract holiday dates from raw text (e.g., Confluence page content). "
            "Handles multiple date formats: 'January 26, 2026', '26-Jan-2026', "
            "'2026-01-26', '26/01/2026', etc. Returns a clean list of YYYY-MM-DD strings."
        ),
        args_schema=ParseHolidayDatesInput,
        when_to_use=[
            "After fetching a Confluence holiday page, to extract dates from the page content",
            "When you have raw text containing dates and need them in YYYY-MM-DD format",
        ],
        when_not_to_use=[
            "When you already have dates in YYYY-MM-DD format",
        ],
        primary_intent=ToolIntent.QUESTION,
        typical_queries=[
            "Parse holiday dates from this Confluence page content",
            "Extract dates from the holiday calendar text",
        ],
        llm_description=(
            "Extracts all recognizable dates from raw text (HTML, markdown, plain text). "
            "Use this after fetching a Confluence holiday page to get clean YYYY-MM-DD dates. "
            "Pass the raw page content as 'text' and optionally filter by 'year'."
        ),
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
    )
    def parse_holiday_dates(self, text: str, year: Optional[int] = None) -> str:
        """Extract dates from raw text content."""
        import re
        from datetime import datetime

        try:
            found_dates: list[dict] = []
            seen: set[str] = set()

            # ── Pattern 1: ISO format YYYY-MM-DD ────────────────────────────
            for match in re.finditer(r'\b(\d{4})-(\d{2})-(\d{2})\b', text):
                try:
                    d = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    iso = d.isoformat()
                    if iso not in seen:
                        seen.add(iso)
                        found_dates.append({"date": iso, "raw": match.group(0)})
                except ValueError:
                    pass

            # ── Pattern 2: DD/MM/YYYY or DD-MM-YYYY ─────────────────────────
            for match in re.finditer(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b', text):
                day, month, yr = int(match.group(1)), int(match.group(2)), int(match.group(3))
                try:
                    d = date(yr, month, day)
                    iso = d.isoformat()
                    if iso not in seen:
                        seen.add(iso)
                        found_dates.append({"date": iso, "raw": match.group(0)})
                except ValueError:
                    # Try MM/DD/YYYY if DD/MM fails
                    try:
                        d = date(yr, day, month)
                        iso = d.isoformat()
                        if iso not in seen:
                            seen.add(iso)
                            found_dates.append({"date": iso, "raw": match.group(0)})
                    except ValueError:
                        pass

            # ── Pattern 3: "Month DD, YYYY" or "DD Month YYYY" ──────────────
            month_names = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
                "jan": 1, "feb": 2, "mar": 3, "apr": 4,
                "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
                "oct": 10, "nov": 11, "dec": 12,
            }

            # "January 26, 2026" or "January 26 2026"
            pattern_mdy = re.compile(
                r'\b(' + '|'.join(month_names.keys()) + r')\s+(\d{1,2}),?\s*(\d{4})\b',
                re.IGNORECASE
            )
            for match in pattern_mdy.finditer(text):
                month_str, day_str, year_str = match.group(1), match.group(2), match.group(3)
                month_num = month_names.get(month_str.lower())
                if month_num:
                    try:
                        d = date(int(year_str), month_num, int(day_str))
                        iso = d.isoformat()
                        if iso not in seen:
                            seen.add(iso)
                            found_dates.append({"date": iso, "raw": match.group(0)})
                    except ValueError:
                        pass

            # "26 January 2026" or "26-Jan-2026" or "26 Jan 2026"
            pattern_dmy = re.compile(
                r'\b(\d{1,2})[\s\-]+(' + '|'.join(month_names.keys()) + r')[\s\-,]+(\d{4})\b',
                re.IGNORECASE
            )
            for match in pattern_dmy.finditer(text):
                day_str, month_str, year_str = match.group(1), match.group(2), match.group(3)
                month_num = month_names.get(month_str.lower())
                if month_num:
                    try:
                        d = date(int(year_str), month_num, int(day_str))
                        iso = d.isoformat()
                        if iso not in seen:
                            seen.add(iso)
                            found_dates.append({"date": iso, "raw": match.group(0)})
                    except ValueError:
                        pass

            # Filter by year if specified
            if year:
                found_dates = [d for d in found_dates if d["date"].startswith(str(year))]

            # Sort by date
            found_dates.sort(key=lambda x: x["date"])

            return json.dumps({
                "holidays": [d["date"] for d in found_dates],
                "total_count": len(found_dates),
                "details": found_dates,
                "year_filter": year,
            })

        except Exception as e:
            return json.dumps({"error": f"Failed to parse dates: {e}"})