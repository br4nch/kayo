from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools import Kayo


async def setup(bot: "Kayo"):
    from .owner import Owner

    await bot.add_cog(Owner(bot))
