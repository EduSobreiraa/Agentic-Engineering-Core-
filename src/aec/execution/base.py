"""Provider-independent execution interface."""

from abc import ABC, abstractmethod

from .models import ExecutionRequest, ExecutionResult


class ExecutionAdapter(ABC):
    @abstractmethod
    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        """Run one turn."""

    @abstractmethod
    async def cancel(self, execution_id: str) -> bool:
        """Stop a live turn, returning whether it was found."""

    @abstractmethod
    async def healthcheck(self) -> bool:
        """Check the installed executor without consuming a model turn."""
