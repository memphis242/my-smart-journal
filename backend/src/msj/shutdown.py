"""Worker shutdown signalling shared by the batch loop and inference providers."""

from typing import Protocol


class StopSignal(Protocol):
    """A shutdown request, such as a threading.Event set by the worker's signal handler."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class ShutdownRequested(Exception):
    """An in-flight job was abandoned because the worker is shutting down.

    The job did not fail: the batch requeues it without consuming an attempt.
    """
