from asyncio import gather
from io import BytesIO
from math import sqrt
from typing import List

from discord import File
from jishaku.functools import executor_function
from PIL import Image

from tools.managers.network import ClientSession


@executor_function
def __colage_open(buffer: BytesIO) -> Image.Image:
    return Image.open(buffer).convert("RGBA").resize((256, 256))


@executor_function
def __colage_paste(background: Image.Image, image: BytesIO, x: int, y: int) -> None:
    background.paste(
        image,
        (
            x * 256,
            y * 256,
        ),
    )


async def collage(session: ClientSession, image_urls: List[str]) -> File:
    images: List[Image.Image] = await gather(
        *[
            __colage_open(BytesIO(buffer))
            for buffer in await gather(
                *[session.request(image_url) for image_url in image_urls]
            )
            if isinstance(buffer, bytes)
        ]
    )

    rows = int(sqrt(len(images)))
    columns = (len(images) + rows - 1) // rows

    background = Image.new(
        "RGBA",
        (
            columns * 256,
            rows * 256,
        ),
    )
    await gather(
        *[
            __colage_paste(background, image, index % columns, index // columns)
            for index, image in enumerate(images)
        ]
    )

    output = BytesIO()
    background.save(
        output,
        format="png",
    )

    output.seek(0)
    background.close()
    for image in images:
        image.close()

    return File(
        output,
        filename="collage.png",
    )
