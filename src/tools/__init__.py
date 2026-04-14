"""Lazy tool exports.

Keep optional integrations out of the import path for the core API.
This prevents one missing optional dependency from breaking app startup.
"""

from importlib import import_module

_EXPORT_MAP = {
    "get_n8n_connector": ("src.tools.n8n_connector", "get_n8n_connector"),
    "N8NConnector": ("src.tools.n8n_connector", "N8NConnector"),
    "ActionType": ("src.tools.n8n_connector", "ActionType"),
    "RiskLevel": ("src.tools.n8n_connector", "RiskLevel"),
    "URLFetcher": ("src.tools.url_fetcher", "URLFetcher"),
    "URLInfo": ("src.tools.url_fetcher", "URLInfo"),
    "INTERNAL_DOMAINS": ("src.tools.url_fetcher", "INTERNAL_DOMAINS"),
    "TRUSTED_DOMAINS": ("src.tools.url_fetcher", "TRUSTED_DOMAINS"),
}


def __getattr__(name: str):
    if name not in _EXPORT_MAP:
        raise AttributeError(f"module 'src.tools' has no attribute {name}")
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    return getattr(module, attr_name)


__all__ = list(_EXPORT_MAP.keys())
