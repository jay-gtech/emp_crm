"""
app/core/limiter.py
===================
Shared slowapi rate-limiter instance.

Import `limiter` anywhere you need to decorate a route:

    from app.core.limiter import limiter

    @router.post("/example")
    @limiter.limit("10/minute")
    def my_route(request: Request, ...):
        ...

The limiter must be mounted on app.state in main.py before first request:

    from app.core.limiter import limiter
    app.state.limiter = limiter
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
