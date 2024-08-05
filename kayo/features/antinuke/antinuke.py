from discord.ext.commands import Cog

from tools.kayo import Kayo
from tools.managers import logging

log = logging.getLogger(__name__)


class Antinuke(Cog):
    def __init__(self, bot: Kayo):
        self.bot: Kayo = bot
