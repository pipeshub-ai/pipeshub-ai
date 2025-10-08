from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleSheetsDataSource:
    """
    Auto-generated Google Sheets API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Sheets API v4 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Google Sheets API client.
        Args:
            client: Google Sheets API client from build('sheets', 'v4', credentials=credentials)
        """
        self.client = client

    async def spreadsheets_create(self) -> Dict[str, Any]:
        """Google Sheets API: Creates a spreadsheet, returning the newly created spreadsheet.

        HTTP POST v4/spreadsheets

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets().create(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_get(
        self,
        spreadsheetId: str,
        ranges: Optional[str] = None,
        includeGridData: Optional[bool] = None,
        excludeTablesInBandedRanges: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Sheets API: Returns the spreadsheet at the given ID. The caller must specify the spreadsheet ID. By default, data within grids is not returned. You can include grid data in one of 2 ways: * Specify a [field mask](https://developers.google.com/workspace/sheets/api/guides/field-masks) listing your desired fields using the `fields` URL parameter in HTTP * Set the includeGridData URL parameter to true. If a field mask is set, the `includeGridData` parameter is ignored For large spreadsheets, as a best practice, retrieve only the specific spreadsheet fields that you want. To retrieve only subsets of spreadsheet data, use the ranges URL parameter. Ranges are specified using [A1 notation](https://developers.google.com/workspace/sheets/api/guides/concepts#cell). You can define a single cell (for example, `A1`) or multiple cells (for example, `A1:D5`). You can also get cells from other sheets within the same spreadsheet (for example, `Sheet2!A1:C4`) or retrieve multiple ranges at once (for example, `?ranges=A1:D5&ranges=Sheet2!A1:C4`). Limiting the range returns only the portions of the spreadsheet that intersect the requested ranges.

        HTTP GET v4/spreadsheets/{spreadsheetId}

        Args:
            spreadsheetId (str, required): The spreadsheet to request.
            ranges (str, optional): The ranges to retrieve from the spreadsheet.
            includeGridData (bool, optional): True if grid data should be returned. This parameter is ignored if a field mask was set in the request.
            excludeTablesInBandedRanges (bool, optional): True if tables should be excluded in the banded ranges. False if not set.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId
        if ranges is not None:
            kwargs['ranges'] = ranges
        if includeGridData is not None:
            kwargs['includeGridData'] = includeGridData
        if excludeTablesInBandedRanges is not None:
            kwargs['excludeTablesInBandedRanges'] = excludeTablesInBandedRanges

        request = self.client.spreadsheets().get(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_get_by_data_filter(
        self,
        spreadsheetId: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Returns the spreadsheet at the given ID. The caller must specify the spreadsheet ID. This method differs from GetSpreadsheet in that it allows selecting which subsets of spreadsheet data to return by specifying a dataFilters parameter. Multiple DataFilters can be specified. Specifying one or more data filters returns the portions of the spreadsheet that intersect ranges matched by any of the filters. By default, data within grids is not returned. You can include grid data one of 2 ways: * Specify a [field mask](https://developers.google.com/workspace/sheets/api/guides/field-masks) listing your desired fields using the `fields` URL parameter in HTTP * Set the includeGridData parameter to true. If a field mask is set, the `includeGridData` parameter is ignored For large spreadsheets, as a best practice, retrieve only the specific spreadsheet fields that you want.

        HTTP POST v4/spreadsheets/{spreadsheetId}:getByDataFilter

        Args:
            spreadsheetId (str, required): The spreadsheet to request.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets().getByDataFilter(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets().getByDataFilter(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_batch_update(
        self,
        spreadsheetId: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Applies one or more updates to the spreadsheet. Each request is validated before being applied. If any request is not valid then the entire request will fail and nothing will be applied. Some requests have replies to give you some information about how they are applied. The replies will mirror the requests. For example, if you applied 4 updates and the 3rd one had a reply, then the response will have 2 empty replies, the actual reply, and another empty reply, in that order. Due to the collaborative nature of spreadsheets, it is not guaranteed that the spreadsheet will reflect exactly your changes after this completes, however it is guaranteed that the updates in the request will be applied together atomically. Your changes may be altered with respect to collaborator changes. If there are no collaborators, the spreadsheet should reflect your changes.

        HTTP POST v4/spreadsheets/{spreadsheetId}:batchUpdate

        Args:
            spreadsheetId (str, required): The spreadsheet to apply the updates to.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets().batchUpdate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets().batchUpdate(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_get(
        self,
        spreadsheetId: str,
        range: str,
        majorDimension: Optional[str] = None,
        valueRenderOption: Optional[str] = None,
        dateTimeRenderOption: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Sheets API: Returns a range of values from a spreadsheet. The caller must specify the spreadsheet ID and a range.

        HTTP GET v4/spreadsheets/{spreadsheetId}/values/{range}

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to retrieve data from.
            range (str, required): The [A1 notation or R1C1 notation](https://developers.google.com/workspace/sheets/api/guides/concepts#cell) of the range to retrieve values from.
            majorDimension (str, optional): The major dimension that results should use. For example, if the spreadsheet data in Sheet1 is: `A1=1,B1=2,A2=3,B2=4`, then requesting `range=Sheet1!A1:B2?majorDimension=ROWS` returns `[[1,2],[3,4]]`, whereas requesting `range=Sheet1!A1:B2?majorDimension=COLUMNS` returns `[[1,3],[2,4]]`.
            valueRenderOption (str, optional): How values should be represented in the output. The default render option is FORMATTED_VALUE.
            dateTimeRenderOption (str, optional): How dates, times, and durations should be represented in the output. This is ignored if value_render_option is FORMATTED_VALUE. The default dateTime render option is SERIAL_NUMBER.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId
        if range is not None:
            kwargs['range'] = range
        if majorDimension is not None:
            kwargs['majorDimension'] = majorDimension
        if valueRenderOption is not None:
            kwargs['valueRenderOption'] = valueRenderOption
        if dateTimeRenderOption is not None:
            kwargs['dateTimeRenderOption'] = dateTimeRenderOption

        request = self.client.spreadsheets_values().get(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_update(
        self,
        spreadsheetId: str,
        range: str,
        valueInputOption: Optional[str] = None,
        includeValuesInResponse: Optional[bool] = None,
        responseValueRenderOption: Optional[str] = None,
        responseDateTimeRenderOption: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Sheets API: Sets values in a range of a spreadsheet. The caller must specify the spreadsheet ID, range, and a valueInputOption.

        HTTP PUT v4/spreadsheets/{spreadsheetId}/values/{range}

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to update.
            range (str, required): The [A1 notation](https://developers.google.com/workspace/sheets/api/guides/concepts#cell) of the values to update.
            valueInputOption (str, optional): How the input data should be interpreted.
            includeValuesInResponse (bool, optional): Determines if the update response should include the values of the cells that were updated. By default, responses do not include the updated values. If the range to write was larger than the range actually written, the response includes all values in the requested range (excluding trailing empty rows and columns).
            responseValueRenderOption (str, optional): Determines how values in the response should be rendered. The default render option is FORMATTED_VALUE.
            responseDateTimeRenderOption (str, optional): Determines how dates, times, and durations in the response should be rendered. This is ignored if response_value_render_option is FORMATTED_VALUE. The default dateTime render option is SERIAL_NUMBER.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId
        if range is not None:
            kwargs['range'] = range
        if valueInputOption is not None:
            kwargs['valueInputOption'] = valueInputOption
        if includeValuesInResponse is not None:
            kwargs['includeValuesInResponse'] = includeValuesInResponse
        if responseValueRenderOption is not None:
            kwargs['responseValueRenderOption'] = responseValueRenderOption
        if responseDateTimeRenderOption is not None:
            kwargs['responseDateTimeRenderOption'] = responseDateTimeRenderOption

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_values().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_values().update(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_append(
        self,
        spreadsheetId: str,
        range: str,
        valueInputOption: Optional[str] = None,
        insertDataOption: Optional[str] = None,
        includeValuesInResponse: Optional[bool] = None,
        responseValueRenderOption: Optional[str] = None,
        responseDateTimeRenderOption: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Sheets API: Appends values to a spreadsheet. The input range is used to search for existing data and find a "table" within that range. Values will be appended to the next row of the table, starting with the first column of the table. See the [guide](https://developers.google.com/workspace/sheets/api/guides/values#appending_values) and [sample code](https://developers.google.com/workspace/sheets/api/samples/writing#append_values) for specific details of how tables are detected and data is appended. The caller must specify the spreadsheet ID, range, and a valueInputOption. The `valueInputOption` only controls how the input data will be added to the sheet (column-wise or row-wise), it does not influence what cell the data starts being written to.

        HTTP POST v4/spreadsheets/{spreadsheetId}/values/{range}:append

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to update.
            range (str, required): The [A1 notation](https://developers.google.com/workspace/sheets/api/guides/concepts#cell) of a range to search for a logical table of data. Values are appended after the last row of the table.
            valueInputOption (str, optional): How the input data should be interpreted.
            insertDataOption (str, optional): How the input data should be inserted.
            includeValuesInResponse (bool, optional): Determines if the update response should include the values of the cells that were appended. By default, responses do not include the updated values.
            responseValueRenderOption (str, optional): Determines how values in the response should be rendered. The default render option is FORMATTED_VALUE.
            responseDateTimeRenderOption (str, optional): Determines how dates, times, and durations in the response should be rendered. This is ignored if response_value_render_option is FORMATTED_VALUE. The default dateTime render option is SERIAL_NUMBER.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId
        if range is not None:
            kwargs['range'] = range
        if valueInputOption is not None:
            kwargs['valueInputOption'] = valueInputOption
        if insertDataOption is not None:
            kwargs['insertDataOption'] = insertDataOption
        if includeValuesInResponse is not None:
            kwargs['includeValuesInResponse'] = includeValuesInResponse
        if responseValueRenderOption is not None:
            kwargs['responseValueRenderOption'] = responseValueRenderOption
        if responseDateTimeRenderOption is not None:
            kwargs['responseDateTimeRenderOption'] = responseDateTimeRenderOption

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_values().append(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_values().append(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_clear(
        self,
        spreadsheetId: str,
        range: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Clears values from a spreadsheet. The caller must specify the spreadsheet ID and range. Only values are cleared -- all other properties of the cell (such as formatting, data validation, etc..) are kept.

        HTTP POST v4/spreadsheets/{spreadsheetId}/values/{range}:clear

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to update.
            range (str, required): The [A1 notation or R1C1 notation](https://developers.google.com/workspace/sheets/api/guides/concepts#cell) of the values to clear.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId
        if range is not None:
            kwargs['range'] = range

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_values().clear(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_values().clear(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_batch_get(
        self,
        spreadsheetId: str,
        ranges: Optional[str] = None,
        majorDimension: Optional[str] = None,
        valueRenderOption: Optional[str] = None,
        dateTimeRenderOption: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Sheets API: Returns one or more ranges of values from a spreadsheet. The caller must specify the spreadsheet ID and one or more ranges.

        HTTP GET v4/spreadsheets/{spreadsheetId}/values:batchGet

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to retrieve data from.
            ranges (str, optional): The [A1 notation or R1C1 notation](https://developers.google.com/workspace/sheets/api/guides/concepts#cell) of the range to retrieve values from.
            majorDimension (str, optional): The major dimension that results should use. For example, if the spreadsheet data is: `A1=1,B1=2,A2=3,B2=4`, then requesting `ranges=["A1:B2"],majorDimension=ROWS` returns `[[1,2],[3,4]]`, whereas requesting `ranges=["A1:B2"],majorDimension=COLUMNS` returns `[[1,3],[2,4]]`.
            valueRenderOption (str, optional): How values should be represented in the output. The default render option is ValueRenderOption.FORMATTED_VALUE.
            dateTimeRenderOption (str, optional): How dates, times, and durations should be represented in the output. This is ignored if value_render_option is FORMATTED_VALUE. The default dateTime render option is SERIAL_NUMBER.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId
        if ranges is not None:
            kwargs['ranges'] = ranges
        if majorDimension is not None:
            kwargs['majorDimension'] = majorDimension
        if valueRenderOption is not None:
            kwargs['valueRenderOption'] = valueRenderOption
        if dateTimeRenderOption is not None:
            kwargs['dateTimeRenderOption'] = dateTimeRenderOption

        request = self.client.spreadsheets_values().batchGet(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_batch_update(
        self,
        spreadsheetId: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Sets values in one or more ranges of a spreadsheet. The caller must specify the spreadsheet ID, a valueInputOption, and one or more ValueRanges.

        HTTP POST v4/spreadsheets/{spreadsheetId}/values:batchUpdate

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_values().batchUpdate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_values().batchUpdate(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_batch_clear(
        self,
        spreadsheetId: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Clears one or more ranges of values from a spreadsheet. The caller must specify the spreadsheet ID and one or more ranges. Only values are cleared -- all other properties of the cell (such as formatting and data validation) are kept.

        HTTP POST v4/spreadsheets/{spreadsheetId}/values:batchClear

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_values().batchClear(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_values().batchClear(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_batch_get_by_data_filter(
        self,
        spreadsheetId: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Returns one or more ranges of values that match the specified data filters. The caller must specify the spreadsheet ID and one or more DataFilters. Ranges that match any of the data filters in the request will be returned.

        HTTP POST v4/spreadsheets/{spreadsheetId}/values:batchGetByDataFilter

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to retrieve data from.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_values().batchGetByDataFilter(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_values().batchGetByDataFilter(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_batch_update_by_data_filter(
        self,
        spreadsheetId: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Sets values in one or more ranges of a spreadsheet. The caller must specify the spreadsheet ID, a valueInputOption, and one or more DataFilterValueRanges.

        HTTP POST v4/spreadsheets/{spreadsheetId}/values:batchUpdateByDataFilter

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_values().batchUpdateByDataFilter(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_values().batchUpdateByDataFilter(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_values_batch_clear_by_data_filter(
        self,
        spreadsheetId: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Clears one or more ranges of values from a spreadsheet. The caller must specify the spreadsheet ID and one or more DataFilters. Ranges matching any of the specified data filters will be cleared. Only values are cleared -- all other properties of the cell (such as formatting, data validation, etc..) are kept.

        HTTP POST v4/spreadsheets/{spreadsheetId}/values:batchClearByDataFilter

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_values().batchClearByDataFilter(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_values().batchClearByDataFilter(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_developer_metadata_get(
        self,
        spreadsheetId: str,
        metadataId: int
    ) -> Dict[str, Any]:
        """Google Sheets API: Returns the developer metadata with the specified ID. The caller must specify the spreadsheet ID and the developer metadata's unique metadataId.

        HTTP GET v4/spreadsheets/{spreadsheetId}/developerMetadata/{metadataId}

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to retrieve metadata from.
            metadataId (int, required): The ID of the developer metadata to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId
        if metadataId is not None:
            kwargs['metadataId'] = metadataId

        request = self.client.spreadsheets_developerMetadata().get(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_developer_metadata_search(
        self,
        spreadsheetId: str
    ) -> Dict[str, Any]:
        """Google Sheets API: Returns all developer metadata matching the specified DataFilter. If the provided DataFilter represents a DeveloperMetadataLookup object, this will return all DeveloperMetadata entries selected by it. If the DataFilter represents a location in a spreadsheet, this will return all developer metadata associated with locations intersecting that region.

        HTTP POST v4/spreadsheets/{spreadsheetId}/developerMetadata:search

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet to retrieve metadata from.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_developerMetadata().search(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_developerMetadata().search(**kwargs) # type: ignore
        return request.execute()

    async def spreadsheets_sheets_copy_to(
        self,
        spreadsheetId: str,
        sheetId: int
    ) -> Dict[str, Any]:
        """Google Sheets API: Copies a single sheet from a spreadsheet to another spreadsheet. Returns the properties of the newly created sheet.

        HTTP POST v4/spreadsheets/{spreadsheetId}/sheets/{sheetId}:copyTo

        Args:
            spreadsheetId (str, required): The ID of the spreadsheet containing the sheet to copy.
            sheetId (int, required): The ID of the sheet to copy.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if spreadsheetId is not None:
            kwargs['spreadsheetId'] = spreadsheetId
        if sheetId is not None:
            kwargs['sheetId'] = sheetId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spreadsheets_sheets().copyTo(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spreadsheets_sheets().copyTo(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
