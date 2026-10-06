from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PenaltyPreview(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    type_id: int = Field(gt=0, le=2147483647)
    type_version: int = Field(gt=0, le=2147483647)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=500)
    calculation_type: Literal["PER_MINUTE", "FIXED", "MANUAL"]
    rate: int = Field(ge=0, le=1000000)
    quantity: int | None = Field(default=None, ge=1, le=10000)
    points: int = Field(ge=1, le=1000000000)
