from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools import Kayo


async def setup(bot: "Kayo"):
    from .network import Network

    await bot.add_cog(Network(bot))
