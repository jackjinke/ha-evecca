"""Serialize session renewal and bound authenticated operation retries."""

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Concatenate

from .api import EveccaApi, EveccaAuthError
from .models import EveccaSession

_RENEWAL_INTERVAL = 6 * 60 * 60


class EveccaSessionManager:
    """Own the active session, publishing renewal only after synchronous persistence."""

    def __init__(
        self,
        api: EveccaApi,
        session: EveccaSession,
        hw_id: str,
        on_refresh: Callable[[EveccaSession], None],
    ) -> None:
        """Accept saved credentials and a synchronous persistence/MQTT callback."""
        self._api = api
        self._session = session
        self._hw_id = hw_id
        self._on_refresh = on_refresh
        self._refresh_lock = asyncio.Lock()
        self._last_refresh = monotonic()
        self._generation = 0
        self._refresh_error: Exception | None = None

    @property
    def session(self) -> EveccaSession:
        """Return the last successfully persisted session."""
        return self._session

    async def async_refresh(self) -> EveccaSession:
        """Force renewal, sharing the outcome with overlapping renewal requests."""
        return await self._async_refresh_generation(self._generation)

    async def async_renew_if_due(self) -> EveccaSession:
        """Renew after six hours; callers supply scheduling, not this manager."""
        if monotonic() - self._last_refresh < _RENEWAL_INTERVAL:
            return self._session
        return await self._async_refresh_generation(self._generation)

    async def async_call[T, **P](
        self,
        operation: Callable[Concatenate[EveccaSession, P], Awaitable[T]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """Renew when due, then replay only one definitive authentication rejection."""
        await self.async_renew_if_due()
        session = self._session
        generation = self._generation
        try:
            return await operation(session, *args, **kwargs)
        except EveccaAuthError:
            await self._async_refresh_generation(generation)
        return await operation(self._session, *args, **kwargs)

    async def _async_refresh_generation(self, generation: int) -> EveccaSession:
        """Coalesce an observed renewal outcome without comparing token strings."""
        async with self._refresh_lock:
            if generation != self._generation:
                if self._refresh_error is not None:
                    raise self._refresh_error
                return self._session
            try:
                refreshed = await self._api.async_token_login(
                    self._session.token, self._session.user_id, self._hw_id
                )
                self._on_refresh(refreshed)
            except Exception as err:
                # Waiting callers share the failure; a later fresh call can retry.
                self._refresh_error = err
                self._generation += 1
                raise
            # No await separates persistence and publication. Cancellation cannot
            # leave a persisted session unpublished, and never poisons the lock.
            self._session = refreshed
            self._last_refresh = monotonic()
            self._refresh_error = None
            self._generation += 1
            return refreshed
