from __future__ import annotations


class CaseBridgeError(RuntimeError):
    pass


class ConfigError(CaseBridgeError):
    pass


class DataError(CaseBridgeError):
    pass


class GeminiError(CaseBridgeError):
    pass
