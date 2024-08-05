from random import choice
from typing import Any, Optional

from munch import Munch

from config import Authorization
from tools.managers.network import ClientSession


class Client(ClientSession):
    def __init__(self: "Client", *args, **kwargs):
        super().__init__(
            base_url="http://ws.audioscrobbler.com",
            *args,
            **kwargs,
        )

    async def request(
        self: "Client", slug: Optional[str] = None, **params: Any
    ) -> Munch:
        data: Munch = await super().request(
            "/2.0/",
            params={
                "api_key": choice(Authorization.Lastfm),
                "format": "json",
                **params,
            },
            slug=slug,
        )
        return data
