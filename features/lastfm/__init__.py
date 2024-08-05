from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools import Kayo


async def setup(bot: "Kayo"):
    from .lastfm import Lastfm

    await bot.add_cog(Lastfm(bot))
