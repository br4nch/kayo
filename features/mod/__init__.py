from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools import Kayo


async def setup(bot: "Kayo"):
    from .mod import Moderation

    await bot.add_cog(Moderation(bot))
