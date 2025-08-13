from enum import Enum


class ParameterType(Enum):
    """Supported parameter types for tool functions"""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"
    BINARY_IO = "binary_io"
    LIST = "List"
    DICT = "Dict"
