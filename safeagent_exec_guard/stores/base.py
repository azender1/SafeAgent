from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class ExecutionStore(ABC):
    @abstractmethod
    def get_execution(self, request_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def create_pending_execution(self, request_id: str, action: str, payload: Dict[str, Any]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def mark_executed(self, request_id: str, execution_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def mark_failed(self, request_id: str, error_text: str) -> None:
        raise NotImplementedError