from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools import Kayo


async def setup(bot: "Kayo"):
    from .antinuke import Antinuke

    await bot.add_cog(Antinuke(bot))
