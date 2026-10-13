from typing import Literal

from pydantic import BaseModel


Backend = Literal['INDEX', 'BERGAMOT', 'GOOGLE', 'TEXTRA']
Output = Literal['translated', 'comparison']


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


class JobView(BaseModel):
    id: str
    filename: str
    backend: Backend
    output: Output
    status: Literal['queued', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled']
    created_at: str
    updated_at: str
    expires_at: str | None
    queue_position: int | None
    progress: ProgressView
    error: ErrorView | None
    downloads: dict[str, str] | None
