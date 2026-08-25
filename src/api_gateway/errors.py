"""api-gateway 异常层次。每类对应一个错误信封 code 与 HTTP 状态码。"""


class GatewayError(Exception):
    """基类。"""

    code = "DOWNSTREAM_ERROR"
    http_status = 502


class BadRequest(GatewayError):
    code = "BAD_REQUEST"
    http_status = 400


class Unauthorized(GatewayError):
    code = "UNAUTHORIZED"
    http_status = 401


class RateLimitExceeded(GatewayError):
    code = "RATE_LIMITED"
    http_status = 429

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"rate limited, retry after {retry_after}s")


class DownstreamError(GatewayError):
    code = "DOWNSTREAM_ERROR"

    def __init__(self, message: str = "downstream error", http_status: int = 502) -> None:
        self.http_status = http_status
        super().__init__(message)


class StreamingNotAvailable(BadRequest):
    """stream=true 但本期不支持流式（design §12：v2 只做变体 A）。"""
