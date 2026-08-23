""" Async dashboard API with timeout handling.

Demonstrates:
- asyncio.gather for concurrent microservice calls
- Per-call timeout with graceful degradation
- Unified Pydantic response model
"""

from __future__ import annotations

import asyncio
import random

from fastapi import APIRouter

from tax_agent_platform.models import (DashboardResponse,
    Notification,
    TaxRecord,
    UserProfile,)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ---- Simulated async microservice calls ----

async def get_profile_async(user_id: int) -> UserProfile:
    """Simulate calling user profile microservice."""
    await asyncio.sleep(random.uniform(0.2, 0.6))
    return UserProfile(name=f"User {user_id}", role="TaxManager")


async def get_tax_records_async(user_id: int) -> list[TaxRecord]:
    """Simulate calling tax records microservice."""
    await asyncio.sleep(random.uniform(0.3, 0.8))
    return [TaxRecord(id=1, status="Filed"), TaxRecord(id=2, status="Pending")]


async def get_notifications_async(user_id: int) -> list[Notification]:
    """Simulate calling notifications microservice."""
    await asyncio.sleep(random.uniform(0.1, 0.4))
    return [Notification(message="New tax law update")]


async def _with_timeout(coro, timeout: float = 2.0):
    """Wrap a coroutine with timeout — returns None on timeout (graceful degradation)."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return None


@router.get("/{user_id}", response_model=DashboardResponse)
async def get_user_dashboard_data(user_id: int) -> DashboardResponse:
    """Fetch dashboard data from 3 microservices concurrently with per-call timeout.

    If any service times out, returns None for that section (graceful degradation)
    rather than failing the entire request.
    """
    profile, records, notifications = await asyncio.gather(_with_timeout(get_profile_async(user_id)),
        _with_timeout(get_tax_records_async(user_id)),
        _with_timeout(get_notifications_async(user_id)),)

    return DashboardResponse(profile=profile,
        recent_records=records,
        notifications=notifications,)
