"""Public errors raised by Axenco protocol conversions and clients."""


class AxencoError(Exception):
    """Base error for the Axenco library."""


class AxencoAuthError(AxencoError):
    """Authentication failed or credentials expired."""


class AxencoConnectionError(AxencoError):
    """The service could not be reached."""


class AxencoResponseError(AxencoError):
    """The service returned an invalid response."""
