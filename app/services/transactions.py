from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Concatenate, Protocol

from app.repositories.base import Repository


class HasRepository(Protocol):
    @property
    def repo(self) -> Repository: ...


def atomic[S: HasRepository, **P, R](
    method: Callable[Concatenate[S, P], Awaitable[R]],
) -> Callable[Concatenate[S, P], Awaitable[R]]:
    """Rollback one operation on failure; the caller owns the outer commit."""

    @wraps(method)
    async def wrapped(self: S, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self.repo.session.begin_nested():
            return await method(self, *args, **kwargs)

    return wrapped
