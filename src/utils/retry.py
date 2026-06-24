from typing import Type, Tuple, Callable, Any
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)
from src.utils.logger import logger

def _log_retry_attempt(retry_state):
    """Callback function for tenacity to log retry attempts using loguru logger."""
    fn_name = retry_state.fn.__name__ if retry_state.fn else "anonymous_function"
    exception = retry_state.outcome.exception()
    next_sleep = retry_state.next_action.sleep if retry_state.next_action else 0
    
    logger.warning(
        f"Attempt #{retry_state.attempt_number} failed for function '{fn_name}'. "
        f"Retrying in {next_sleep:.2f}s... Exception: {exception}"
    )

def retry_on_exception(
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    max_attempts: int = 3,
    multiplier: float = 1.0,
    min_delay: float = 1.0,
    max_delay: float = 10.0
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to retry functions (both sync or async) with exponential backoff.
    
    Args:
        exceptions: A tuple of exception classes that should trigger a retry.
        max_attempts: Maximum number of execution attempts.
        multiplier: Multiplier for exponential backoff delay calculation.
        min_delay: Minimum wait time before retrying (in seconds).
        max_delay: Maximum wait time before retrying (in seconds).
    
    Usage:
        @retry_on_exception(exceptions=(requests.RequestException,), max_attempts=3)
        def fetch_data():
            ...
            
        @retry_on_exception(exceptions=(httpx.HTTPError,), max_attempts=4)
        async def fetch_data_async():
            ...
    """
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=multiplier, min=min_delay, max=max_delay),
        retry=retry_if_exception_type(exceptions),
        before_sleep=_log_retry_attempt
    )