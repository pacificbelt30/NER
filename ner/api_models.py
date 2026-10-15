from typing import Literal

from pydantic import BaseModel, Field


Backend = Literal['INDEX', 'BERGAMOT', 'GOOGLE', 'TEXTRA']
Output = Literal['translated', 'comparison']
JobStatus = Literal['queued', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled']


class ErrorView(BaseModel):
    code: str
    message: str
    retryable: bool


class ProgressView(BaseModel):
    stage: str
    completed_blocks: int
    total_blocks: int
    page: int
    pages: int
    block_id: str | None = None
    completed_chunks: int = 0
    total_chunks: int = 0


class WarningView(BaseModel):
    code: str
    stage: str
    page: int | None = None
    block_id: str | None = None
    message: str


class ResultSummaryView(BaseModel):
    outcome: Literal['complete', 'partial', 'untranslated', 'complete_with_warnings']
    total_blocks: int
    translated_blocks: int
    retained_blocks: int
    warning_count: int
    source_pages: int
    report_pages: int


class JobView(BaseModel):
    id: str
    filename: str
    backend: Backend
    output: Output
    status: JobStatus
    created_at: str
    updated_at: str
    expires_at: str | None
    queue_position: int | None
    progress: ProgressView
    error: ErrorView | None
    downloads: dict[str, str] | None
    warnings: list[WarningView] = Field(default_factory=list)
    result_summary: ResultSummaryView | None = None


class JobListView(BaseModel):
    items: list[JobView]
    total: int
    limit: int
    offset: int


class ServerSettings(BaseModel):
    model_config = {'extra': 'forbid'}
    retention_days: int | None = Field(..., ge=1, le=3650, strict=True)
