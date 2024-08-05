from io import BytesIO
from os import mkdir, path
from sys import getsizeof
from typing import Optional

import magic
from aiofiles import open as async_open
from pydantic import BaseModel
from xxhash import xxh32_hexdigest

from tools.managers.network import ClientSession

cache = path.join("/tmp", "cache")
if not path.exists(cache):
    mkdir(cache)


class Asset(BaseModel):
    size: int
    name: str
    extension: str
    is_video: bool
    buffer: bytes

    @property
    def url(self: "Asset") -> str:
        return f"https://dev.wock.sh/cache/{self.name}.{self.extension}"


async def save(
    session: ClientSession,
    buffer: BytesIO | bytes | str,
    redistribute: bool = True,
    name: Optional[str] = None,
    prefix: Optional[str] = "",
) -> Asset:
    if isinstance(buffer, BytesIO):
        buffer = buffer.getvalue()

    elif isinstance(buffer, str):
        buffer: bytes = await session.request(buffer)

    name = prefix + (name or xxh32_hexdigest(buffer, seed=58589))
    mime, extension = magic.from_buffer(buffer, mime=True).split("/")

    if redistribute:
        file = path.join(cache, f"{name}.{extension}")

        if not path.exists(file):
            async with async_open(file, "wb") as new_file:
                await new_file.write(buffer)

    return Asset(
        name=name,
        extension=extension,
        size=getsizeof(buffer),
        is_video=mime.startswith("video"),
        buffer=buffer,
    )
