from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools import Kayo


async def setup(bot: "Kayo"):
    from .jishaku import Jishaku

    await bot.add_cog(Jishaku(bot=bot))
