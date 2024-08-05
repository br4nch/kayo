from asyncio import TimeoutError
from contextlib import suppress
from random import shuffle
from time import time
from typing import List, Optional

from discord import ClientException, Embed, Member, Message, VoiceState
from discord.ext.commands import Cog, Range, command
from discord.ext.commands.context import Context
from discord.opus import OpusNotLoaded
from pomice import Node, NodePool, Playlist, Track, TrackLoadError, TrackType

from config import Authorization, Lavalink
from tools.kayo import Kayo
from tools.managers.context import Context
from tools.utilities import Error, natural_duration, shorten

from .player import Player


class Music(Cog):
    def __init__(self, bot: Kayo):
        self.bot: Kayo = bot
        self.node: Node
        self.bot.loop.create_task(self.authenticate())

    async def authenticate(self: "Music"):
        self.node = await NodePool().create_node(
            bot=self.bot,
            log_handler=None,
            host=Lavalink.host,
            port=Lavalink.port,
            password=Lavalink.password,
            identifier=f"kayo{str(time())}",
            spotify_client_id=Authorization.Spotify.client_id,
            spotify_client_secret=Authorization.Spotify.client_secret,
        )

    async def cog_before_invoke(self, ctx: Context):
        if ctx.command:
            ctx.player = await self.player(
                ctx,
                connect=ctx.command.name == "play",
            )

    @Cog.listener()
    async def on_pomice_track_end(
        self: "Music",
        player: Player,
        track: Optional[Track],
        reason: str,
    ):
        await player.start()

    @Cog.listener()
    async def on_voice_state_update(
        self: "Music",
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ):
        if member != self.bot.user or after.channel:
            return

        if not (player := self.node.get_player(member.guild.id)):
            return

        await player.destroy()

    async def player(self: "Music", ctx: Context, *, connect: bool = False) -> Player:
        if not (voice := ctx.author.voice) or not voice.channel:
            raise Error("You are not connected to a voice channel!")

        elif (bot_voice := ctx.guild.me.voice) and voice.channel != bot_voice.channel:
            raise Error("You are not connected to my voice channel!")

        elif not bot_voice or not (player := self.node.get_player(ctx.guild.id)):
            if not connect:
                raise Error("I'm not connected to a voice channel!")

            try:
                player = await voice.channel.connect(
                    cls=Player,
                    self_deaf=True,
                )
                player.invoke = ctx.channel
            except (TimeoutError, ClientException, OpusNotLoaded) as exception:
                raise Error(
                    f"I was not able to connect to {voice.channel.mention}!"
                ) from exception

            await player.set_volume(65)

        return player  # type: ignore

    @command(
        name="queue",
        aliases=["q"],
    )
    async def queue(self: "Music", ctx: Context) -> Message:
        """
        View all tracks in the queue.
        """

        if not ctx.player.queue:
            if not (track := ctx.player.current):
                return await ctx.notice("There isn't a track playing!")

            return await ctx.send(
                embed=Embed(
                    title="Currently Playing",
                    description=(
                        f"[{track}]({track.uri})"
                        + (
                            f"\n> **{track.author}**"
                            if track.track_type != TrackType.YOUTUBE
                            else ""
                        )
                    ),
                )
            )

        tracks = [
            f"[{shorten(track.title)}]({track.uri}) requested by **{track.requester or 'Unknown User'}**"
            for track in ctx.player.queue._queue
        ]

        embed = Embed(
            title=f"Queue for {ctx.player.channel}",
            description=(
                (
                    f"Playing [{shorten(track.title)}]({track.uri}) "
                    + (
                        f"by **{track.author}** "
                        if track.track_type != TrackType.YOUTUBE
                        else ""
                    )
                    + f"`{natural_duration(ctx.player.position)}`/`{natural_duration(track.length)}`\n"
                    + f"> Requested by **{track.requester or 'Unknown User'}**"
                )
                if (track := ctx.player.current)
                else ""
            ),
        )
        if track := ctx.player.current:
            if track.track_type != TrackType.YOUTUBE:
                embed.set_thumbnail(url=track.thumbnail)

        return await ctx.paginate(
            tracks,
            embed=embed,
        )

    @command(
        name="play",
        aliases=["p"],
    )
    async def play(self: "Music", ctx: Context, *, query: str) -> Optional[Message]:
        """
        Add a track to the queue.
        """

        result: Optional[List[Track] | Playlist] = None
        with suppress(TrackLoadError):
            result = await ctx.player.get_tracks(
                query,
                ctx=ctx,
            )

        if not result:
            return await ctx.notice("No tracks were found for that query.")

        if isinstance(result, Playlist):
            for track in result.tracks:
                await ctx.player.insert(track)

            await ctx.approve(
                f"Added **{result.track_count} tracks** from [{result}]({result.uri}) to the queue."
            )
        else:
            track = result[0]
            await ctx.player.insert(track)

            if ctx.player.is_playing:
                await ctx.approve(f"Added [{track}]({track.uri}) to the queue.")

        if not ctx.player.is_playing:
            await ctx.player.start()

    @command(
        name="skip",
        aliases=["sk"],
    )
    async def skip(self: "Music", ctx: Context) -> Optional[Message]:
        """
        Skip the current track.
        """

        if not ctx.player.queue:
            return await ctx.notice("There aren't anymore tracks in the queue!")

        await ctx.player.skip()
        await ctx.add_check()

    @command(
        name="shuffle",
        aliases=["mix"],
    )
    async def shuffle(self: "Music", ctx: Context) -> Optional[Message]:
        """
        Shuffle the music queue.
        """

        if not ctx.player.queue:
            return await ctx.notice("There aren't any tracks in the queue!")

        shuffle(ctx.player.queue._queue)
        await ctx.add_check()

    @command(
        name="move",
        aliases=["mv"],
    )
    async def move(self: "Music", ctx: Context, _from: int, to: int) -> Message:
        """
        Move a track to a different position.
        """

        if _from == to:
            return await ctx.send_help(ctx.command)

        queue = ctx.player.queue._queue
        try:
            queue[_from - 1]
            queue[to - 1]
        except IndexError:
            return await ctx.notice("The track position doesn't exist!")

        track = queue[_from - 1]
        del queue[_from - 1]
        queue.insert(to - 1, track)
        return await ctx.approve(
            f"Moved [{track.title}]({track.uri}) to position `{to}`."
        )

    @command(
        name="remove",
        aliases=["rmv", "del"],
    )
    async def remove(self: "Music", ctx: Context, index: int) -> Message:
        """
        Remove a track from the queue.
        """

        queue = ctx.player.queue._queue
        if index < 1 or index > len(queue):
            return await ctx.notice(f"Track doesn't exist at position `{index}`!")

        track = queue[index - 1]
        del queue[index - 1]
        return await ctx.approve(
            f"Removed [{track.title}]({track.uri}) from the queue."
        )

    @command(
        name="volume",
        aliases=["vol"],
    )
    async def volume(
        self: "Music",
        ctx: Context,
        percentage: Optional[Range[int, 1, 100]],
    ) -> Message:
        """
        Set the music volume.
        """

        if not percentage:
            return await ctx.neutral(f"Current volume: `{ctx.player.volume}%`.")

        await ctx.player.set_volume(percentage)
        return await ctx.approve(f"Set the volume to `{percentage}%`.")

    @command(name="pause")
    async def pause(
        self: "Music",
        ctx: Context,
    ) -> Message:
        """
        Pause the current track.
        """

        if ctx.player.is_paused:
            return await ctx.notice("The track is already paused!")

        await ctx.player.set_pause(True)
        return await ctx.add_check()

    @command(name="resume")
    async def resume(
        self: "Music",
        ctx: Context,
    ) -> Message:
        """
        Resume the current track.
        """

        if not ctx.player.is_paused:
            return await ctx.notice("The track isn't paused!")

        await ctx.player.set_pause(False)
        return await ctx.add_check()

    @command(
        name="disconnect",
        aliases=["dc"],
    )
    async def disconnect(self: "Music", ctx: Context) -> None:
        """
        Destroy the music player.
        """

        await ctx.player.destroy()
        await ctx.add_check()
