from pydantic import BaseModel, ConfigDict


class PipelinePolicy(BaseModel):
    """Per-organization switches for optional stages."""

    model_config = ConfigDict(frozen=True)

    classification: bool = True


DEFAULT_POLICY = PipelinePolicy()
