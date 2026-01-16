from __future__ import annotations

class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

class ParseError(AppError):
    def __init__(self, message="Invalid JSON from Bedrock"):
        super().__init__("PARSE_ERROR", message, 502)

class SchemaError(AppError):
    def __init__(self, message="Schema validation failed"):
        super().__init__("SCHEMA_ERROR", message, 502)

class SemanticError(AppError):
    def __init__(self, message="Semantic validation failed"):
        super().__init__("SEMANTIC_ERROR", message, 502)
