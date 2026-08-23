from __future__ import annotations

from fastapi import HTTPException

from .db import get_db
from .service import Bundle, load_bundle


def db_dep():
    return get_db()


async def bundle_or_404(
    account_id: str, preset: str | None = "30d", start: str | None = None, end: str | None = None,
    with_views: bool = True,
) -> Bundle:
    bundle = await load_bundle(
        get_db(), account_id, preset=preset, start=start, end=end, with_views=with_views
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"No account {account_id}")
    return bundle
