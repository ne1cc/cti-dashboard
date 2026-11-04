"""Keep patient search values out of application and server request logs."""

from contextvars import ContextVar

patient_request: ContextVar[bool] = ContextVar("patient_request", default=False)
