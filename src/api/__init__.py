"""API package public entrypoint."""

from . import app as app_module

app = app_module.app
llm_client = app_module.llm_client
redis_cache = app_module.redis_cache
supervisor = app_module.supervisor
async_session = app_module.async_session


def __getattr__(name: str):
    return getattr(app_module, name)

__all__ = ["app", "llm_client", "redis_cache", "supervisor", "async_session"]
