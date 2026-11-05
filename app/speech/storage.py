"""Audio storage: local disk in dev (served at /media), Supabase Storage in prod (private bucket,
signed URLs). Manifests store storage *paths*; the API turns them into playable URLs at read time."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx


class AudioStorage(Protocol):
    async def put(self, path: str, data: bytes, content_type: str = "audio/mpeg") -> None: ...

    async def url(self, path: str, expires_s: int = 6 * 3600) -> str: ...

    async def delete_prefix(self, prefix: str) -> int: ...

    async def delete_older_than(self, cutoff: datetime) -> int: ...


class LocalDiskStorage:
    def __init__(self, root: str | Path, base_url: str = "/media"):
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")

    def _p(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("path escapes storage root")
        return p

    async def put(self, path: str, data: bytes, content_type: str = "audio/mpeg") -> None:
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    async def url(self, path: str, expires_s: int = 6 * 3600) -> str:
        return f"{self.base_url}/{quote(path)}"

    async def delete_prefix(self, prefix: str) -> int:
        base = self._p(prefix)
        if not base.exists():
            return 0
        files = [base] if base.is_file() else [f for f in base.rglob("*") if f.is_file()]
        for f in files:
            f.unlink()
        return len(files)

    async def delete_older_than(self, cutoff: datetime) -> int:
        if not self.root.exists():
            return 0
        n = 0
        ts = cutoff.timestamp()
        for f in self.root.rglob("*"):
            if f.is_file() and f.stat().st_mtime < ts:
                f.unlink()
                n += 1
        return n


class SupabaseStorage:
    """Supabase Storage REST API with the service-role key (server side only)."""

    def __init__(
        self,
        supabase_url: str,
        service_key: str,
        bucket: str = "brief-audio",
        client: httpx.AsyncClient | None = None,
    ):
        if not (supabase_url and service_key):
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for Supabase storage")
        self.base = supabase_url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
        self.client = client or httpx.AsyncClient(timeout=30)
        self._signed: dict[str, tuple[float, str]] = {}

    async def put(self, path: str, data: bytes, content_type: str = "audio/mpeg") -> None:
        r = await self.client.post(
            f"{self.base}/object/{self.bucket}/{quote(path)}",
            headers={**self.headers, "Content-Type": content_type, "x-upsert": "true"},
            content=data,
        )
        r.raise_for_status()

    async def url(self, path: str, expires_s: int = 6 * 3600) -> str:
        hit = self._signed.get(path)
        if hit and hit[0] > time.time() + 600:
            return hit[1]
        r = await self.client.post(
            f"{self.base}/object/sign/{self.bucket}/{quote(path)}",
            headers=self.headers,
            json={"expiresIn": expires_s},
        )
        r.raise_for_status()
        signed = r.json()["signedURL"]
        full = self.base + signed if signed.startswith("/") else signed
        self._signed[path] = (time.time() + expires_s, full)
        return full

    async def _list(self, prefix: str) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            r = await self.client.post(
                f"{self.base}/object/list/{self.bucket}",
                headers=self.headers,
                json={"prefix": prefix, "limit": 1000, "offset": offset},
            )
            r.raise_for_status()
            page = r.json()
            for o in page:
                name = f"{prefix.rstrip('/')}/{o['name']}" if prefix else o["name"]
                if o.get("id") is None:  # a folder
                    out.extend(await self._list(name))
                else:
                    out.append({**o, "path": name})
            if len(page) < 1000:
                return out
            offset += 1000

    async def _remove(self, paths: list[str]) -> int:
        for i in range(0, len(paths), 500):
            r = await self.client.request(
                "DELETE",
                f"{self.base}/object/{self.bucket}",
                headers=self.headers,
                json={"prefixes": paths[i : i + 500]},
            )
            r.raise_for_status()
        return len(paths)

    async def delete_prefix(self, prefix: str) -> int:
        return await self._remove([o["path"] for o in await self._list(prefix)])

    async def delete_older_than(self, cutoff: datetime) -> int:
        old = []
        for o in await self._list(""):
            created = o.get("created_at")
            if created and datetime.fromisoformat(created.replace("Z", "+00:00")) < cutoff:
                old.append(o["path"])
        return await self._remove(old)
