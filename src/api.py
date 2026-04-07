"""Legacy compatibility entrypoint.

Prefer importing from ``src.api`` or running ``python -m src.api.app`` going
forward. This module is kept as a thin compatibility shim.
"""

from src.api import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
    )
