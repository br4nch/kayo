from asyncio import Queue as DefaultQueue
from asyncio import QueueFull, TimeoutError
from collections import deque
from typing import TYPE_CHECKING, Optional

from async_timeout import timeout
from discord import Embed
from pomice import Player as DefaultPlayer
from pomice import Track, TrackType

from tools.managers import logging
from tools.utilities import Error

if TYPE_CHECKING:
    from discord.abc import MessageableChannel

log = logging.getLogger(__name__)


class Queue(DefaultQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._queue: deque[Track] = deque(maxlen=500)

    def __bool__(self) -> bool:
        return bool(self._queue)


class Player(DefaultPlayer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = Queue()
        self.invoke: MessageableChannel
        self.waiting = False

    async def insert(self: "Player", track: Track) -> Track:
        try:
            self.queue.put_nowait(track)
        except QueueFull as exception:
            log.warn(f"Reached the max queue in {self.channel} ({self.channel.id})!")
            raise Error(
                f"The queue is currently full! (`{self.queue.maxsize}` tracks)"
            ) from exception

        return track

    async def start(self: "Player") -> Optional[Track]:
        if self.is_playing or self.waiting:
            return

        track: Track
        self.waiting = True

        try:
            async with timeout(180):
                track = await self.queue.get()
                self.waiting = False
        except TimeoutError:
            return await self.destroy()

        if self.invoke:
            embed = Embed(
                color=0x2B2D31,
                title="Now Playing",
                description=f"> [*`{track}`*]({track.uri})",
            )
            if track.track_type == TrackType.YOUTUBE:
                embed.set_image(url=track.thumbnail)
            else:
                embed.set_thumbnail(url=track.thumbnail)

            await self.invoke.send(embed=embed)

        log.info(f"Now playing {track} in {self.channel} ({self.channel.id}).")
        return await self.play(track)

    async def skip(self: "Player"):
        if self.is_paused:
            await self.set_pause(False)

        return await self.stop()

    async def destroy(self):
        if self.guild.id in self._node._players:
            log.info(f"Gracefully destroyed player in {self.guild} ({self.guild.id}).")

            await super().destroy()
