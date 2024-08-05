from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools import Kayo


async def setup(bot: "Kayo"):
    from .servers import Servers

    await bot.add_cog(Servers(bot))
