from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools import Kayo


async def setup(bot: "Kayo"):
    from .misc import Miscellaneous

    await bot.add_cog(Miscellaneous(bot))
