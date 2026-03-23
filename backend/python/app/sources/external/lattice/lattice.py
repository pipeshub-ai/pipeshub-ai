from typing import Any, Dict, List, Optional

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.lattice.lattice import LatticeClient, LatticeResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400
class LatticeDataSource:
    """Comprehensive Lattice API client wrapper.

    Provides async methods for ALL Lattice API endpoints across:

    TALENT API (v1):
    - Competencies (get by ID)
    - Custom Attributes (get by ID, values)
    - Departments (get by ID, list all)
    - Feedback (get by ID, list all with filters)
    - Goals (CRUD, updates, progress tracking)
    - Me (current authenticated user)
    - Questions (get by ID, revisions)
    - Review Cycles (get by ID, list all, reviewees, reviews)
    - Reviewees (get by ID, reviews)
    - Tags (get by ID, list all)
    - Tasks (get by ID)
    - Updates (get by ID, list all)
    - Users (get by ID, list all, custom attributes, direct reports, goals, tasks)

    HRIS API (v2):
    - Employees (CRUD, list all, get fields)
    - Time Off Requests (list all, get by ID)
    - Time Off Policies (list all)
    - Time Off Reports (list all with filtering)

    Pagination:
    - v1: cursor-based (limit, startingAfter, endingCursor, hasMore)
    - v2: offset-based (limit, offset, total)

    Rate Limits:
    - v1: 240 requests/minute
    - v2: 200 requests/minute, 3000 requests/hour

    All methods return LatticeResponse objects with standardized format.
    Every parameter matches Lattice official API documentation exactly.
    No **kwargs usage - all parameters are explicitly typed.
    """

    def __init__(self, client: LatticeClient) -> None:
        """Initialize with LatticeClient."""
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'LatticeDataSource':
        """Return the data source instance."""
        return self


    async def get_competency(
        self,
        competency_id: str
    ) -> LatticeResponse:
        """Returns a competency with the given id. Returns 404 if not found

        Args:
            competency_id: The ID of the competency

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/competency/{competency_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_custom_attribute(
        self,
        custom_attribute_id: str
    ) -> LatticeResponse:
        """Returns the custom attribute with the given id. Returns 404 if not found

        Args:
            custom_attribute_id: The ID of the custom attribute

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/customAttribute/{custom_attribute_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_custom_attribute_value(
        self,
        custom_attribute_value_id: str
    ) -> LatticeResponse:
        """Returns the custom attribute value with the given id. Returns 404 if not found

        Args:
            custom_attribute_value_id: The ID of the custom attribute value

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/customAttributeValue/{custom_attribute_value_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_department(
        self,
        department_id: str
    ) -> LatticeResponse:
        """Returns a department with the given id. Returns 404 if not found

        Args:
            department_id: The ID of the department

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/department/{department_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_departments(
        self,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all departments in Lattice

        Args:
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/departments"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_feedback(
        self,
        feedback_id: str
    ) -> LatticeResponse:
        """Returns a feedback with the given id. Returns 404 if not found

        Args:
            feedback_id: The ID of the feedback

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/feedback/{feedback_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_feedbacks(
        self,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
        only_public: Optional[bool] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all continuous feedback in Lattice. Newest feedback returned first

        Args:
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor
            only_public: Filter to only return public feedback

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/feedbacks"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after
            if only_public is not None:
                _params["onlyPublic"] = only_public

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_goal(
        self,
        goal_id: str
    ) -> LatticeResponse:
        """Returns a goal with the given id. Returns 404 if not found

        Args:
            goal_id: The ID of the goal

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/goal/{goal_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_goals(
        self,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
        state: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of goals in Lattice

        Args:
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor
            state: Filter goals by state (e.g. active, draft, completed, archived)

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/goals"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after
            if state is not None:
                _params["state"] = state

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def create_goal(
        self,
        name: str,
        description: Optional[str] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
        priority: Optional[int] = None,
        private: Optional[bool] = None,
        owner_ids: Optional[List[str]] = None,
        okr_type: Optional[str] = None,
        amount_type: Optional[str] = None,
        starting_amount: Optional[float] = None,
        goal_amount: Optional[float] = None,
        company_goal: Optional[bool] = None,
        department_id: Optional[str] = None,
        departments_visible_to_ids: Optional[List[str]] = None,
        goal_cycle_id: Optional[str] = None,
        tag_names: Optional[List[str]] = None,
        is_draft: Optional[bool] = None,
        parent_id: Optional[str] = None
    ) -> LatticeResponse:
        """Creates a new goal with the provided information

        Args:
            name: The name/objective of the goal
            description: The markdown text description of the goal
            start_date: The start date of the goal (YYYY-MM-DD)
            due_date: The due date of the goal (YYYY-MM-DD)
            priority: The priority of the goal
            private: Whether the goal is private
            owner_ids: List of user IDs who own this goal
            okr_type: The OKR type of the goal
            amount_type: The type of amount being tracked
            starting_amount: The starting amount when the goal was set
            goal_amount: The target amount to reach
            company_goal: Whether this is a company-wide goal
            department_id: The department ID if this is a department goal
            departments_visible_to_ids: List of department IDs that can view this goal
            goal_cycle_id: The goal cycle ID to associate with
            tag_names: List of tag names to associate with the goal
            is_draft: Whether this goal is a draft
            parent_id: The parent goal ID in the goal tree

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/goals"

            _data["name"] = name
            if description is not None:
                _data["description"] = description
            if start_date is not None:
                _data["startDate"] = start_date
            if due_date is not None:
                _data["dueDate"] = due_date
            if priority is not None:
                _data["priority"] = priority
            if private is not None:
                _data["private"] = private
            if owner_ids is not None:
                _data["ownerIds"] = owner_ids
            if okr_type is not None:
                _data["okrType"] = okr_type
            if amount_type is not None:
                _data["amountType"] = amount_type
            if starting_amount is not None:
                _data["startingAmount"] = starting_amount
            if goal_amount is not None:
                _data["goalAmount"] = goal_amount
            if company_goal is not None:
                _data["companyGoal"] = company_goal
            if department_id is not None:
                _data["departmentId"] = department_id
            if departments_visible_to_ids is not None:
                _data["departmentsVisibleToIds"] = departments_visible_to_ids
            if goal_cycle_id is not None:
                _data["goalCycleId"] = goal_cycle_id
            if tag_names is not None:
                _data["tagNames"] = tag_names
            if is_draft is not None:
                _data["isDraft"] = is_draft
            if parent_id is not None:
                _data["parentId"] = parent_id

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                body=_data if _data else None,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def update_goal(
        self,
        goal_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
        priority: Optional[int] = None,
        private: Optional[bool] = None,
        owner_ids: Optional[List[str]] = None,
        okr_type: Optional[str] = None,
        amount_type: Optional[str] = None,
        starting_amount: Optional[float] = None,
        goal_amount: Optional[float] = None,
        company_goal: Optional[bool] = None,
        department_id: Optional[str] = None,
        departments_visible_to_ids: Optional[List[str]] = None,
        goal_cycle_id: Optional[str] = None,
        tag_names: Optional[List[str]] = None,
        is_draft: Optional[bool] = None,
        parent_id: Optional[str] = None
    ) -> LatticeResponse:
        """Updates an existing goal with the provided information

        Args:
            goal_id: The ID of the goal to update
            name: The name/objective of the goal
            description: The markdown text description of the goal
            start_date: The start date of the goal (YYYY-MM-DD)
            due_date: The due date of the goal (YYYY-MM-DD)
            priority: The priority of the goal
            private: Whether the goal is private
            owner_ids: List of user IDs who own this goal
            okr_type: The OKR type of the goal
            amount_type: The type of amount being tracked
            starting_amount: The starting amount when the goal was set
            goal_amount: The target amount to reach
            company_goal: Whether this is a company-wide goal
            department_id: The department ID if this is a department goal
            departments_visible_to_ids: List of department IDs that can view this goal
            goal_cycle_id: The goal cycle ID to associate with
            tag_names: List of tag names to associate with the goal
            is_draft: Whether this goal is a draft
            parent_id: The parent goal ID in the goal tree

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/goals/{goal_id}"

            if name is not None:
                _data["name"] = name
            if description is not None:
                _data["description"] = description
            if start_date is not None:
                _data["startDate"] = start_date
            if due_date is not None:
                _data["dueDate"] = due_date
            if priority is not None:
                _data["priority"] = priority
            if private is not None:
                _data["private"] = private
            if owner_ids is not None:
                _data["ownerIds"] = owner_ids
            if okr_type is not None:
                _data["okrType"] = okr_type
            if amount_type is not None:
                _data["amountType"] = amount_type
            if starting_amount is not None:
                _data["startingAmount"] = starting_amount
            if goal_amount is not None:
                _data["goalAmount"] = goal_amount
            if company_goal is not None:
                _data["companyGoal"] = company_goal
            if department_id is not None:
                _data["departmentId"] = department_id
            if departments_visible_to_ids is not None:
                _data["departmentsVisibleToIds"] = departments_visible_to_ids
            if goal_cycle_id is not None:
                _data["goalCycleId"] = goal_cycle_id
            if tag_names is not None:
                _data["tagNames"] = tag_names
            if is_draft is not None:
                _data["isDraft"] = is_draft
            if parent_id is not None:
                _data["parentId"] = parent_id

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                body=_data if _data else None,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_all_goal_updates(
        self,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Retrieves all goal updates across all goals in the company with pagination support

        Args:
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/goals/updates"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def create_goal_update(
        self,
        goal_id: str,
        comment: Optional[str] = None,
        status: Optional[str] = None,
        increment: Optional[float] = None,
        complete: Optional[bool] = None,
        incomplete: Optional[bool] = None
    ) -> LatticeResponse:
        """Creates a progress update for an existing goal

        Args:
            goal_id: The ID of the goal to update
            comment: The comment for the progress update
            status: The status of the goal update
            increment: The amount to increment the goal progress by
            complete: Mark the goal as complete
            incomplete: Mark the goal as incomplete

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/goals/{goal_id}/update"

            if comment is not None:
                _data["comment"] = comment
            if status is not None:
                _data["status"] = status
            if increment is not None:
                _data["increment"] = increment
            if complete is not None:
                _data["complete"] = complete
            if incomplete is not None:
                _data["incomplete"] = incomplete

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                body=_data if _data else None,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_goal_updates(
        self,
        goal_id: str,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Retrieves all progress updates for a specific goal with pagination support

        Args:
            goal_id: The ID of the goal
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/goals/{goal_id}/updates"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_me(
        self,
    ) -> LatticeResponse:
        """Returns the current user the API token is associated with

        Args:

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/me"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_question(
        self,
        question_id: str
    ) -> LatticeResponse:
        """Returns a review question with the given id. Returns 404 if not found

        Args:
            question_id: The ID of the question

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/question/{question_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_question_revision(
        self,
        question_revision_id: str
    ) -> LatticeResponse:
        """Returns a question revision with the given id. Returns 404 if not found

        Args:
            question_revision_id: The ID of the question revision

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/questionRevision/{question_revision_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_review_cycle(
        self,
        review_cycle_id: str
    ) -> LatticeResponse:
        """Returns a review cycle with the given id. Returns 404 if not found

        Args:
            review_cycle_id: The ID of the review cycle

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/reviewCycle/{review_cycle_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_review_cycle_reviewees(
        self,
        review_cycle_id: str,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all reviewees in a review cycle

        Args:
            review_cycle_id: The ID of the review cycle
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/reviewCycle/{review_cycle_id}/reviewees"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_review_cycle_reviews(
        self,
        review_cycle_id: str,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
        order_direction: Optional[str] = None,
        offset: Optional[int] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all reviews for a review cycle

        Args:
            review_cycle_id: The ID of the review cycle
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor
            order_direction: Sort direction for results (asc or desc)
            offset: Offset for pagination

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/reviewCycle/{review_cycle_id}/reviews"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after
            if order_direction is not None:
                _params["orderDirection"] = order_direction
            if offset is not None:
                _params["offset"] = offset

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_review_cycles(
        self,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all review cycles in Lattice

        Args:
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/reviewCycles"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_reviewee(
        self,
        reviewee_id: str
    ) -> LatticeResponse:
        """Returns a reviewee with the given id. Returns 404 if not found

        Args:
            reviewee_id: The ID of the reviewee

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/reviewee/{reviewee_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_reviewee_reviews(
        self,
        reviewee_id: str,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all reviews for a reviewee

        Args:
            reviewee_id: The ID of the reviewee
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/reviewee/{reviewee_id}/reviews"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_tag(
        self,
        tag_id: str
    ) -> LatticeResponse:
        """Returns a tag with the given id. Returns 404 if not found

        Args:
            tag_id: The ID of the tag

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/tag/{tag_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_tags(
        self,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all tags in Lattice

        Args:
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/tags"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_task(
        self,
        task_id: str
    ) -> LatticeResponse:
        """Returns a task with the given id. Returns 404 if not found

        Args:
            task_id: The ID of the task

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/task/{task_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_update(
        self,
        update_id: str
    ) -> LatticeResponse:
        """Returns an Update with the given id. Returns 404 if not found

        Args:
            update_id: The ID of the update

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/update/{update_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_updates(
        self,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all Updates in Lattice

        Args:
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/updates"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_user(
        self,
        user_id: str
    ) -> LatticeResponse:
        """Returns a user with the given id. Returns 404 if not found

        Args:
            user_id: The ID of the user

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/user/{user_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_user_custom_attributes(
        self,
        user_id: str,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a list of the user's custom attributes

        Args:
            user_id: The ID of the user
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/user/{user_id}/customAttributes"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_user_direct_reports(
        self,
        user_id: str
    ) -> LatticeResponse:
        """Returns a list of users that report to a user

        Args:
            user_id: The ID of the user

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/user/{user_id}/directReports"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_user_goals(
        self,
        user_id: str,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
        state: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all goals owned by a user

        Args:
            user_id: The ID of the user
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor
            state: Filter goals by state (e.g. active, draft, completed, archived)

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/user/{user_id}/goals"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after
            if state is not None:
                _params["state"] = state

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_user_tasks(
        self,
        user_id: str
    ) -> LatticeResponse:
        """Returns a paginated list of tasks for a user

        Args:
            user_id: The ID of the user

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/user/{user_id}/tasks"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_users(
        self,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
        status: Optional[str] = None
    ) -> LatticeResponse:
        """Returns a paginated list of users in Lattice. By default returns active users. A status of null retrieves all users regardless of status

        Args:
            limit: Number of objects to return (1-100, default 10)
            starting_after: Cursor for pagination from previous response endingCursor
            status: Filter users by status (active, inactive, null for all)

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v1/users"

            if limit is not None:
                _params["limit"] = limit
            if starting_after is not None:
                _params["startingAfter"] = starting_after
            if status is not None:
                _params["status"] = status

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_employees(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> LatticeResponse:
        """Returns a paginated list of all employees. Uses offset-based pagination

        Args:
            limit: Maximum records per response (1-100, default 25)
            offset: Pagination offset (default 0)

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/employees"

            if limit is not None:
                _params["limit"] = limit
            if offset is not None:
                _params["offset"] = offset

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def create_employee(
        self,
        personal: Optional[Dict[str, Any]] = None,
        contact_information: Optional[Dict[str, Any]] = None,
        employment_details: Optional[Dict[str, Any]] = None,
        role_details: Optional[Dict[str, Any]] = None,
        pay_types: Optional[Dict[str, Any]] = None,
        demographic: Optional[Dict[str, Any]] = None,
        sensitive_data: Optional[Dict[str, Any]] = None,
        custom_categories: Optional[Dict[str, Any]] = None
    ) -> LatticeResponse:
        """Creates a new employee with the provided information. Returns the new employee UUID

        Args:
            personal: Personal info: birthdate, company_employee_id, legal_first_name, legal_middle_name, legal_last_name, preferred_first_name, preferred_last_name
            contact_information: Contact info: personal_email, phone_number, work_email, address_line_1, address_line_2, address_city, address_state, address_country, address_postal_code
            employment_details: Employment details: employment_status, employment_type, start_date, termination_date, termination_reason, termination_type
            role_details: Role details: department, manager, effective_at, job_title
            pay_types: Pay info: base_pay_amount, base_pay_currency, base_pay_effective_at, base_pay_schedule, base_pay_payment_type
            demographic: Demographic info: gender_identity, binary_sex
            sensitive_data: Sensitive data: ssn
            custom_categories: Custom category fields defined by the organization

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/employees"

            if personal is not None:
                _data["personal"] = personal
            if contact_information is not None:
                _data["contact_information"] = contact_information
            if employment_details is not None:
                _data["employment_details"] = employment_details
            if role_details is not None:
                _data["role_details"] = role_details
            if pay_types is not None:
                _data["pay_types"] = pay_types
            if demographic is not None:
                _data["demographic"] = demographic
            if sensitive_data is not None:
                _data["sensitive_data"] = sensitive_data
            if custom_categories is not None:
                _data["custom_categories"] = custom_categories

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                body=_data if _data else None,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_employee(
        self,
        employee_id: str
    ) -> LatticeResponse:
        """Returns a single employee by UUID

        Args:
            employee_id: The UUID of the employee

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/employees/{employee_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def update_employee(
        self,
        employee_id: str,
        personal: Optional[Dict[str, Any]] = None,
        contact_information: Optional[Dict[str, Any]] = None,
        employment_details: Optional[Dict[str, Any]] = None,
        role_details: Optional[Dict[str, Any]] = None,
        pay_types: Optional[Dict[str, Any]] = None,
        demographic: Optional[Dict[str, Any]] = None,
        sensitive_data: Optional[Dict[str, Any]] = None,
        custom_categories: Optional[Dict[str, Any]] = None
    ) -> LatticeResponse:
        """Updates an employee by UUID. Only include fields to update. Returns 204 No Content on success

        Args:
            employee_id: The UUID of the employee to update
            personal: Personal info to update
            contact_information: Contact info to update
            employment_details: Employment details to update
            role_details: Role details to update
            pay_types: Pay info to update
            demographic: Demographic info to update
            sensitive_data: Sensitive data to update
            custom_categories: Custom category fields to update

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/employees/{employee_id}"

            if personal is not None:
                _data["personal"] = personal
            if contact_information is not None:
                _data["contact_information"] = contact_information
            if employment_details is not None:
                _data["employment_details"] = employment_details
            if role_details is not None:
                _data["role_details"] = role_details
            if pay_types is not None:
                _data["pay_types"] = pay_types
            if demographic is not None:
                _data["demographic"] = demographic
            if sensitive_data is not None:
                _data["sensitive_data"] = sensitive_data
            if custom_categories is not None:
                _data["custom_categories"] = custom_categories

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers=_headers,
                body=_data if _data else None,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_employee_fields(
        self,
    ) -> LatticeResponse:
        """Returns all employee field definitions including custom fields, types, and options

        Args:

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/employee-fields"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_time_off_requests(
        self,
        employee_ids: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> LatticeResponse:
        """Returns a paginated list of time off requests with optional filtering

        Args:
            employee_ids: Filter by employee IDs
            start_date: Filter by start date (YYYY-MM-DD)
            end_date: Filter by end date (YYYY-MM-DD)
            statuses: Filter by statuses: PENDING, APPROVED, REJECTED, CANCELLED, PENDING_CANCELLATION
            limit: Maximum records per response (1-100, default 25)
            offset: Pagination offset (default 0)

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/time-off/requests"

            if employee_ids is not None:
                _params["employee_ids"] = employee_ids
            if start_date is not None:
                _params["start_date"] = start_date
            if end_date is not None:
                _params["end_date"] = end_date
            if statuses is not None:
                _params["statuses"] = statuses
            if limit is not None:
                _params["limit"] = limit
            if offset is not None:
                _params["offset"] = offset

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_time_off_request(
        self,
        time_off_request_id: str
    ) -> LatticeResponse:
        """Returns a single time off request by UUID

        Args:
            time_off_request_id: The UUID of the time off request

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/time-off/requests/{time_off_request_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_time_off_policies(
        self,
    ) -> LatticeResponse:
        """Returns all time off policies including policy type and accrual type

        Args:

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/time-off/policies"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def list_time_off_reports(
        self,
        employee_ids: Optional[List[str]] = None,
        policy_ids: Optional[List[str]] = None,
        range_start_date: Optional[str] = None,
        range_end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> LatticeResponse:
        """Returns time off balance reports with optional filtering

        Args:
            employee_ids: Filter by specific employee IDs
            policy_ids: Filter by specific policy IDs
            range_start_date: Start date for report range (YYYY-MM-DD)
            range_end_date: End date for report range (YYYY-MM-DD)
            limit: Maximum records per response (1-100, default 25)
            offset: Pagination offset (default 0)

        Returns:
            LatticeResponse with operation result
        """
        try:
            _params = {}
            _data = {}
            _headers = {}
            url = f"{self.base_url}/v2/time-off/reports"

            if employee_ids is not None:
                _params["employee_ids"] = employee_ids
            if policy_ids is not None:
                _params["policy_ids"] = policy_ids
            if range_start_date is not None:
                _params["range_start_date"] = range_start_date
            if range_end_date is not None:
                _params["range_end_date"] = range_end_date
            if limit is not None:
                _params["limit"] = limit
            if offset is not None:
                _params["offset"] = offset

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params
            )
            response = await self.http.execute(
                request=request
            )

            return LatticeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.is_json and response.status < HTTP_ERROR_THRESHOLD else None,
                error=response.text() if response.status >= HTTP_ERROR_THRESHOLD else None,
                status_code=response.status
            )

        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )


    async def get_api_info(self) -> LatticeResponse:
        """Get information about available API methods."""
        try:
            info = {
                'total_methods': 43,
                'talent_api_v1_methods': 34,
                'hris_api_v2_methods': 9,
                'api_coverage': [
                    'Talent API v1: Competencies, Custom Attributes, Departments',
                    'Talent API v1: Feedback, Goals (CRUD + Updates)',
                    'Talent API v1: Me, Questions, Question Revisions',
                    'Talent API v1: Review Cycles, Reviewees, Reviews',
                    'Talent API v1: Tags, Tasks, Updates, Users',
                    'HRIS API v2: Employees (CRUD + Fields)',
                    'HRIS API v2: Time Off (Requests, Policies, Reports)',
                ],
                'authentication': 'Bearer Token (API Key)',
                'base_url_us': 'https://api.latticehq.com',
                'base_url_emea': 'https://api.emea.latticehq.com',
            }
            return LatticeResponse(
                success=True,
                data=info
            )
        except Exception as e:
            return LatticeResponse(
                success=False,
                error=str(e)
            )
