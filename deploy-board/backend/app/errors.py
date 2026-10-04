"""Domain errors raised by the service layer; the API layer maps them to HTTP responses."""


class DeployBoardError(Exception):
    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(DeployBoardError):
    status = 404


class Conflict(DeployBoardError):
    status = 409


class Forbidden(DeployBoardError):
    status = 403


class Unauthorized(DeployBoardError):
    status = 401


class Invalid(DeployBoardError):
    status = 422
