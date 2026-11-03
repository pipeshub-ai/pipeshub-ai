"""What a model call is for."""

from __future__ import annotations

from enum import Enum


class Workload(str, Enum):
    CHAT = "chat"
    AGENT = "agent"
    SEARCH = "search"
    QUERY_EMBEDDING = "query_embedding"
    SPEECH = "speech"
    INDEXING = "indexing"
    EXTRACTION = "extraction"
    PARSING = "parsing"
    VLM_OCR = "vlm_ocr"
    IMAGE_DESCRIPTION = "image_description"
    ENTITY_RESOLUTION = "entity_resolution"
    HEALTH_CHECK = "health_check"
    DISCOVERY = "discovery"
    SYSTEM = "system"


class WorkloadClass(str, Enum):
    INTERACTIVE = "interactive"
    BACKGROUND = "background"
    ADMIN = "admin"


_CLASS_BY_WORKLOAD = {
    Workload.CHAT: WorkloadClass.INTERACTIVE,
    Workload.AGENT: WorkloadClass.INTERACTIVE,
    Workload.SEARCH: WorkloadClass.INTERACTIVE,
    Workload.QUERY_EMBEDDING: WorkloadClass.INTERACTIVE,
    Workload.SPEECH: WorkloadClass.INTERACTIVE,
    Workload.INDEXING: WorkloadClass.BACKGROUND,
    Workload.EXTRACTION: WorkloadClass.BACKGROUND,
    Workload.PARSING: WorkloadClass.BACKGROUND,
    Workload.VLM_OCR: WorkloadClass.BACKGROUND,
    Workload.IMAGE_DESCRIPTION: WorkloadClass.BACKGROUND,
    Workload.ENTITY_RESOLUTION: WorkloadClass.BACKGROUND,
    Workload.SYSTEM: WorkloadClass.BACKGROUND,
    Workload.HEALTH_CHECK: WorkloadClass.ADMIN,
    Workload.DISCOVERY: WorkloadClass.ADMIN,
}


def workload_class(workload: Workload) -> WorkloadClass:
    return _CLASS_BY_WORKLOAD[workload]
