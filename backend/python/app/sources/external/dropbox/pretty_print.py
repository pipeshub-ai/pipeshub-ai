import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Dict, List, Union

# Define a recursive type for JSON-serializable objects
JSONValue = Union[str, int, float, bool, None, "JSONObject", "JSONArray"]
JSONObject = Dict[str, JSONValue]
JSONArray = List[JSONValue]

def serialize(obj: object) -> JSONValue:
    # basic types
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()

    # collections
    if isinstance(obj, (list, tuple, set)):
        return [serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): serialize(v) for k, v in obj.items()}

    # dataclasses
    if is_dataclass(obj):
        return serialize(asdict(obj))

    # Dropbox SDK / attrs objects
    # they usually have __slots__, so vars(obj) works
    try:
        return {k: serialize(v) for k, v in vars(obj).items()}
    except TypeError:
        # fallback: pull public attributes from dir()
        return {
            k: serialize(getattr(obj, k))
            for k in dir(obj)
            if not k.startswith("_") and not callable(getattr(obj, k))
        }

def to_pretty_json(resp) -> str:
    raw = resp.to_dict() if hasattr(resp, "to_dict") else resp
    return json.dumps(serialize(raw), indent=2, ensure_ascii=False)
