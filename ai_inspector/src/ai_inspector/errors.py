from typing import Any


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }
