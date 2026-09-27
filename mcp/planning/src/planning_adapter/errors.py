"""Typed domain errors preserving the Planning MCP public strings."""


class PlanningValidationError(ValueError):
    """Safe client-facing planning validation failure."""

    def __init__(self, code: str, hint: str | None = None) -> None:
        """Store the code separately; message is 'CODE' or 'CODE: hint'."""
        self.code = code
        super().__init__(code if hint is None else f"{code}: {hint}")


class PlanningRuntimeError(RuntimeError):
    """Safe client-facing planning runtime failure."""

    def __init__(self, code: str) -> None:
        """Store the code separately; message is the bare code."""
        self.code = code
        super().__init__(code)
