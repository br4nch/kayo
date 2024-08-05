from asyncio import gather, sleep
from math import ceil
from time import perf_counter
from typing import Any, AsyncGenerator, List, Optional, Tuple

from discord import Color, Embed, HTTPException, Member, Message, NotFound
from discord.ext.commands import Cog, command, group, param
from discord.ext.commands.context import Context
from humanize import intcomma as comma
from munch import Munch
from yarl import URL

from tools.kayo import Kayo
from tools.managers import Context, Username, database, logging
from tools.utilities import Error, plural, shorten

from .client import Client
from .converters import Album, Artist, Timeframe, Track

log = logging.getLogger(__name__)


class Lastfm(Cog):
    def __init__(self, bot: Kayo):
        self.bot: Kayo = bot
        self.client = Client()
        self.tasks: List[int] = []

    def url(self: "Lastfm", value: str) -> URL:
        return URL(f"https://last.fm/music/{value}")

    async def cog_unload(self: "Lastfm"):
        await self.client.close()
        return await super().cog_unload()

    async def cog_before_invoke(self: "Lastfm", ctx: Context) -> bool:
        if not ctx.command:
            return False

        if ctx.command.qualified_name in (
            "lastfm",
            "lastfm set",
            "fm",
        ):
            return True

        if not (
            data := await self.bot.db.fetchrow(
                """
                SELECT *
                FROM lastfm.config
                WHERE user_id = $1
                """,
                ctx.author.id,
            )
        ):
            raise Error("You haven't connected your Last.fm account!")

        ctx.lastfm = data
        return True

    async def index(
        self: "Lastfm", user: Munch | database.Record
    ) -> AsyncGenerator[Tuple[str, List[Any]], None]:
        if isinstance(user, database.Record):
            user = await self.client.request(
                method="user.getinfo",
                username=user.username,
                slug="user",
            )

        for library in ("artists", "albums", "tracks"):
            pages = ceil(int(user.get(f"{library[:-1]}_count", 0)) / 1000)

            items = []
            for page in await gather(
                *[
                    self.client.request(
                        method=f"user.gettop{library}",
                        slug=f"top{library}.{library[:-1]}",
                        username=user.name,
                        limit=1000,
                        page=page + 1,
                    )
                    for page in range(pages)
                ]
            ):
                items.extend(page)

            yield library, items

    @command(
        name="fm",
        aliases=["now", "np"],
    )
    async def fm(
        self: "Lastfm",
        ctx: Context,
        member: Optional[Member],
    ) -> Message:
        """
        View your current Last.fm track.
        """

        member = member or ctx.author

        if not (
            data := await self.bot.db.fetchrow(
                """
                SELECT * 
                FROM lastfm.config
                WHERE user_id = $1
                """,
                member.id,
            )
        ):
            return await ctx.notice(
                "You haven't connected your Last.fm account!"
                if member == ctx.author
                else f"`{member}` hasn't connected their Last.fm account!"
            )

        tracks, user = await gather(
            *[
                self.client.request(
                    method="user.getrecenttracks",
                    username=data.username,
                    slug="recenttracks.track",
                    limit=1,
                ),
                self.client.request(
                    method="user.getinfo",
                    username=data.username,
                    slug="user",
                ),
            ]
        )
        if not tracks:
            return await ctx.notice(
                f"Recent tracks aren't available for `{data.username}`!"
            )

        track = tracks[0]
        artist = track.artist["#text"]
        track.data = (
            await self.client.request(
                method="track.getinfo",
                username=data.username,
                track=track.name,
                artist=artist,
                slug="track",
            )
            or track
        )

        embed = Embed(color=data.color)
        embed.set_author(
            url=user.url,
            name=user.name,
            icon_url=user.image[-1]["#text"].replace(".png", ".gif"),
        )
        embed.set_thumbnail(url=track.image[-1]["#text"])

        embed.add_field(
            name="Track",
            value=f"[{track.name}]({track.url})",
            inline=len(track.name) <= 20,
        )
        embed.add_field(
            name="Artist",
            value=f"[{artist}]({self.url(artist)})",
            inline=len(artist) <= 20,
        )

        embed.set_footer(
            text=(
                f"Plays: {comma(track.data.userplaycount or 0)} ∙ "
                f"Scrobbles: {comma(user.playcount)} ∙ "
                f"Album: {shorten(track.album.get('#text', 'N/A'), 16)}"
            ),
        )

        message = await ctx.send(embed=embed)
        reactions = data.reactions or ["🔥", "🗑"]
        for reaction in reactions:
            self.bot.ioloop.add_callback(
                message.add_reaction,
                reaction,
            )

        return message

    @group(
        name="lastfm",
        aliases=["lfm", "lf"],
    )
    async def lastfm(self: "Lastfm", ctx: Context) -> Optional[Message]:
        """
        Interact with Last.fm through the bot.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.notice(
                f"View a list of Last.fm commands with `{ctx.prefix}help {ctx.invoked_with}`"
            )

    @lastfm.command(
        name="set",
        aliases=["link"],
    )
    async def lastfm_set(
        self: "Lastfm",
        ctx: Context,
        username: str = param(
            converter=Username(1, 15),
            description="Your Last.fm username",
        ),
    ) -> Message:
        """
        Connect your Last.fm account.
        """

        if ctx.author.id in self.tasks:
            return await ctx.notice(
                "Your current library is being indexed, please try again later!"
            )

        data = await self.client.request(
            method="user.getinfo",
            username=username,
            slug="user",
        )

        self.tasks.append(ctx.author.id)
        await self.bot.db.execute(
            """
            INSERT INTO lastfm.config (user_id, username) 
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET username = EXCLUDED.username
            """,
            ctx.author.id,
            data.name,
        )

        message = await ctx.approve(
            f"Your Last.fm account has been set as [`{data.name}`]({data.url})!"
        )

        start = perf_counter()
        await gather(
            *[
                self.bot.db.execute(query, ctx.author.id)
                for query in (
                    "DELETE FROM lastfm.artists WHERE user_id = $1",
                    "DELETE FROM lastfm.albums WHERE user_id = $1",
                    "DELETE FROM lastfm.tracks WHERE user_id = $1",
                    "DELETE FROM lastfm.crowns WHERE user_id = $1",
                )
            ]
        )

        async for library, items in self.index(user=data):
            if library == "artists":
                await self.bot.db.executemany(
                    """
                    INSERT INTO lastfm.artists
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, artist)
                    DO UPDATE SET
                    plays = EXCLUDED.plays
                    """,
                    [
                        (
                            ctx.author.id,
                            data.name,
                            artist.name,
                            int(artist.playcount),
                        )
                        for artist in items
                    ],
                )

            elif library == "albums":
                await self.bot.db.executemany(
                    """
                    INSERT INTO lastfm.albums
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, artist, album)
                    DO UPDATE SET
                    plays = EXCLUDED.plays
                    """,
                    [
                        (
                            ctx.author.id,
                            data.name,
                            album.artist.name,
                            album.name,
                            int(album.playcount),
                        )
                        for album in items
                    ],
                )

            elif library == "tracks":
                await self.bot.db.executemany(
                    """
                    INSERT INTO lastfm.tracks
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, artist, track)
                    DO UPDATE SET
                    plays = EXCLUDED.plays
                    """,
                    [
                        (
                            ctx.author.id,
                            data.name,
                            track.artist.name,
                            track.name,
                            int(track.playcount),
                        )
                        for track in items
                    ],
                )

        elapsed = perf_counter() - start
        log.info(f"Succesfully indexed {data.name}'s library in {elapsed:.2f}s.")

        self.tasks.remove(ctx.author.id)
        return message

    @lastfm.command(name="update", aliases=["refresh", "index"])
    async def lastfm_update(self: "Lastfm", ctx: Context) -> Message:
        """
        Refresh your local Last.fm library.
        """

        if ctx.author.id in self.tasks:
            return await ctx.notice(
                "Your library is already being indexed, please try again later!"
            )

        self.tasks.append(ctx.author.id)
        await ctx.neutral("Starting index of your Last.fm library...")

        start = perf_counter()
        await gather(
            *[
                self.bot.db.execute(query, ctx.author.id)
                for query in (
                    "DELETE FROM lastfm.artists WHERE user_id = $1",
                    "DELETE FROM lastfm.albums WHERE user_id = $1",
                    "DELETE FROM lastfm.tracks WHERE user_id = $1",
                    "DELETE FROM lastfm.crowns WHERE user_id = $1",
                )
            ]
        )

        async for library, items in self.index(user=ctx.lastfm):
            if library == "artists":
                await self.bot.db.executemany(
                    """
                    INSERT INTO lastfm.artists
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, artist)
                    DO UPDATE SET
                    plays = EXCLUDED.plays
                    """,
                    [
                        (
                            ctx.author.id,
                            ctx.lastfm.username,
                            artist.name,
                            int(artist.playcount),
                        )
                        for artist in items
                    ],
                )
                await self.bot.db.executemany(
                    """
                    UPDATE lastfm.crowns
                    SET plays = $3
                    WHERE user_id = $1
                    AND artist = $2
                    """,
                    [
                        (
                            ctx.author.id,
                            artist.name,
                            int(artist.playcount),
                        )
                        for artist in items
                    ],
                )

            elif library == "albums":
                await self.bot.db.executemany(
                    """
                    INSERT INTO lastfm.albums
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, artist, album)
                    DO UPDATE SET
                    plays = EXCLUDED.plays
                    """,
                    [
                        (
                            ctx.author.id,
                            ctx.lastfm.username,
                            album.artist.name,
                            album.name,
                            int(album.playcount),
                        )
                        for album in items
                    ],
                )

            elif library == "tracks":
                await self.bot.db.executemany(
                    """
                    INSERT INTO lastfm.tracks
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, artist, track)
                    DO UPDATE SET
                    plays = EXCLUDED.plays
                    """,
                    [
                        (
                            ctx.author.id,
                            ctx.lastfm.username,
                            track.artist.name,
                            track.name,
                            int(track.playcount),
                        )
                        for track in items
                    ],
                )

            await ctx.neutral(
                f"Stored `{len(items):,}` {library} from your Last.fm library...",
                patch=ctx.response,
            )

        elapsed = perf_counter() - start
        log.info(
            f"Succesfully indexed {ctx.lastfm.username}'s library in {elapsed:.2f}s."
        )

        await sleep(1)
        self.tasks.remove(ctx.author.id)
        return await ctx.approve(
            "Your Last.fm library has been refreshed.", patch=ctx.response
        )

    @lastfm.command(
        name="color",
        aliases=["colour"],
    )
    async def lastfm_color(
        self: "Lastfm",
        ctx: Context,
        color: Color,
    ) -> Message:
        """
        Set a custom now playing embed color.
        """

        await self.bot.db.execute(
            """
            UPDATE lastfm.config
            SET color = $2
            WHERE user_id = $1
            """,
            ctx.author.id,
            color.value,
        )
        return await ctx.approve(
            f"Your Last.fm embed color has been set as `{color}`!",
            color=color,
        )

    @lastfm.command(
        name="reactions",
        aliases=["reacts", "react"],
    )
    async def lastfm_reactions(
        self: "Lastfm",
        ctx: Context,
        upvote: str,
        downvote: str,
    ) -> Optional[Message]:
        """
        Set a custom upvote and downvote reaction.
        """

        if upvote == downvote:
            return await ctx.send_help(ctx.command)

        for reaction in (upvote, downvote):
            try:
                await ctx.message.add_reaction(reaction)
            except (HTTPException, NotFound, TypeError):
                return await ctx.notice(
                    f"I'm not capable of using **{reaction}**, try using an emoji from this server!"
                )

        await self.bot.db.execute(
            """
            UPDATE lastfm.config
            SET reactions = $2
            WHERE user_id = $1
            """,
            ctx.author.id,
            [upvote, downvote],
        )
        return await ctx.approve(
            f"Your Last.fm reactions have been set as {upvote} and {downvote}"
        )

    @lastfm.command(
        name="recent",
        aliases=["lp"],
    )
    async def lastfm_recent(
        self: "Lastfm",
        ctx: Context,
        member: Optional[Member],
    ) -> Message:
        """
        View your recent tracks.
        """

        member = member or ctx.author

        if not (
            data := await self.bot.db.fetchrow(
                """
                SELECT * 
                FROM lastfm.config
                WHERE user_id = $1
                """,
                member.id,
            )
        ):
            return await ctx.notice(
                "You haven't connected your Last.fm account!"
                if member == ctx.author
                else f"`{member}` hasn't connected their Last.fm account!"
            )

        tracks = await self.client.request(
            method="user.getrecenttracks",
            slug="recenttracks.track",
            username=data.username,
            limit=100,
        )
        if not tracks:
            return await ctx.notice(
                f"Recent tracks aren't available for `{data.username}`!"
            )

        return await ctx.paginate(
            [
                (
                    f"[{track.name}]({track.url}) by **{track.artist['#text']}**"
                    + (f" *<t:{track.date.uts}:R>*" if track.date else "")
                )
                for track in tracks[:100]
            ],
            embed=Embed(
                color=ctx.lastfm.color,
                title=f"Recent tracks for {data.username}",
            ),
        )

    @lastfm.command(
        name="topartists",
        aliases=[
            "artists",
            "tar",
            "ta",
        ],
    )
    async def lastfm_topartists(
        self: "Lastfm",
        ctx: Context,
        member: Optional[Member],
        timeframe: Timeframe = param(
            default=Timeframe("overall"),
            description="The backlog period.",
        ),
    ) -> Message:
        """
        View your overall top artists.
        """

        member = member or ctx.author

        if not (
            data := await self.bot.db.fetchrow(
                """
                SELECT * 
                FROM lastfm.config
                WHERE user_id = $1
                """,
                member.id,
            )
        ):
            return await ctx.notice(
                "You haven't connected your Last.fm account!"
                if member == ctx.author
                else f"`{member}` hasn't connected their Last.fm account!"
            )

        artists = await self.client.request(
            method="user.gettopartists",
            slug="topartists.artist",
            username=data.username,
            period=timeframe.period,
            limit=10,
        )
        if not artists:
            return await ctx.notice(f"`{data.username}` doesn't have any top artists!")

        return await ctx.paginate(
            [
                f"[{artist.name}]({artist.url}) ({plural(artist.playcount):play})"
                for artist in artists
            ],
            embed=Embed(
                color=ctx.lastfm.color,
                title=f"{data.username}'s {timeframe} top artists",
            ),
        )

    @lastfm.command(
        name="topalbums",
        aliases=[
            "albums",
            "tab",
            "tal",
        ],
    )
    async def lastfm_topalbums(
        self: "Lastfm",
        ctx: Context,
        member: Optional[Member],
        timeframe: Timeframe = param(
            default=Timeframe("overall"),
            description="The backlog period.",
        ),
    ) -> Message:
        """
        View your overall top albums.
        """

        member = member or ctx.author

        if not (
            data := await self.bot.db.fetchrow(
                """
                SELECT * 
                FROM lastfm.config
                WHERE user_id = $1
                """,
                member.id,
            )
        ):
            return await ctx.notice(
                "You haven't connected your Last.fm account!"
                if member == ctx.author
                else f"`{member}` hasn't connected their Last.fm account!"
            )

        albums = await self.client.request(
            method="user.gettopalbums",
            slug="topalbums.album",
            username=data.username,
            period=timeframe.period,
            limit=10,
        )
        if not albums:
            return await ctx.notice(f"`{data.username}` doesn't have any top albums!")

        return await ctx.paginate(
            [
                f"[{album.name}]({album.url}) by **{album.artist.name}** ({plural(album.playcount):play})"
                for album in albums
            ],
            embed=Embed(
                color=ctx.lastfm.color,
                title=f"{data.username}'s {timeframe} top albums",
            ),
        )

    @lastfm.command(
        name="toptracks",
        aliases=[
            "tracks",
            "ttr",
            "tt",
        ],
    )
    async def lastfm_toptracks(
        self: "Lastfm",
        ctx: Context,
        member: Optional[Member],
        timeframe: Timeframe = param(
            default=Timeframe("overall"),
            description="The backlog period.",
        ),
    ) -> Message:
        """
        View your overall top tracks.
        """

        member = member or ctx.author

        if not (
            data := await self.bot.db.fetchrow(
                """
                SELECT * 
                FROM lastfm.config
                WHERE user_id = $1
                """,
                member.id,
            )
        ):
            return await ctx.notice(
                "You haven't connected your Last.fm account!"
                if member == ctx.author
                else f"`{member}` hasn't connected their Last.fm account!"
            )

        tracks = await self.client.request(
            method="user.gettoptracks",
            slug="toptracks.track",
            username=data.username,
            period=timeframe.period,
            limit=10,
        )
        if not tracks:
            return await ctx.notice(f"`{data.username}` doesn't have any top tracks!")

        return await ctx.paginate(
            [
                f"[{track.name}]({track.url}) by **{track.artist.name}** ({plural(track.playcount):play})"
                for track in tracks
            ],
            embed=Embed(
                color=ctx.lastfm.color,
                title=f"{data.username}'s {timeframe} top tracks",
            ),
        )

    @lastfm.command(
        name="whoknows",
        aliases=["wk"],
    )
    async def lastfm_whoknows(
        self: "Lastfm",
        ctx: Context,
        *,
        artist: str = param(
            converter=Artist,
            default=Artist.fallback,
        ),
    ) -> Message:
        """
        View the top listeners for an artist.
        """

        records = await self.bot.db.fetch(
            """
            SELECT user_id, username, plays
            FROM lastfm.artists
            WHERE user_id = ANY($2::BIGINT[])
            AND artist = $1
            ORDER BY plays DESC
            """,
            artist,
            [user.id for user in ctx.guild.members],
        )
        if not records:
            return await ctx.notice(
                f"Nobody in this server has listened to `{artist}`!"
            )

        items = []
        for index, listener in enumerate(records[:100], start=1):
            user = ctx.guild.get_member(listener.user_id)
            if not user:
                continue

            rank = f"`{index}`"
            if index == 1:
                rank = "👑"

            items.append(
                f"{rank} [{shorten(user.name, 19)}](https://last.fm/user/{listener.username}) has {plural(listener.plays, md='**'):play}"
            )

        return await ctx.paginate(
            items,
            embed=Embed(
                color=ctx.lastfm.color,
                title=f"Top listeners for {shorten(artist, 12)}",
            ),
            counter=False,
        )

    @lastfm.command(
        name="wkalbum",
        aliases=["whoknowsalbum", "wka"],
    )
    async def lastfm_wkalbum(
        self: "Lastfm",
        ctx: Context,
        *,
        album: Album = param(
            default=Album.fallback,
        ),
    ) -> Message:
        """
        View the top listeners for an album.
        """

        records = await self.bot.db.fetch(
            """
            SELECT user_id, username, plays
            FROM lastfm.albums
            WHERE user_id = ANY($3::BIGINT[])
            AND album = $1
            AND artist = $2
            ORDER BY plays DESC
            """,
            album.name,
            album.artist,
            [user.id for user in ctx.guild.members],
        )
        if not records:
            return await ctx.notice(
                f"Nobody in this server has listened to `{album.name}` by *`{album.artist}`*!"
            )

        items = []
        for index, listener in enumerate(records[:100], start=1):
            user = ctx.guild.get_member(listener.user_id)
            if not user:
                continue

            rank = f"`{index}`"
            if index == 1:
                rank = "👑"

            items.append(
                f"{rank} [{shorten(user.name, 19)}](https://last.fm/user/{listener.username}) has {plural(listener.plays, md='**'):play}"
            )

        return await ctx.paginate(
            items,
            embed=Embed(
                color=ctx.lastfm.color,
                title=f"Top listeners for {shorten(album.name, 12)} by {shorten(album.artist, 12)}",
            ),
            counter=False,
        )

    @lastfm.command(
        name="wktrack",
        aliases=["whoknowstrack", "wkt"],
    )
    async def lastfm_wktrack(
        self: "Lastfm",
        ctx: Context,
        *,
        track: Track = param(
            default=Track.fallback,
        ),
    ) -> Message:
        """
        View the top listeners for a track.
        """

        records = await self.bot.db.fetch(
            """
            SELECT user_id, username, plays
            FROM lastfm.tracks
            WHERE user_id = ANY($3::BIGINT[])
            AND track = $1
            AND artist = $2
            ORDER BY plays DESC
            """,
            track.name,
            track.artist,
            [user.id for user in ctx.guild.members],
        )
        if not records:
            return await ctx.notice(
                f"Nobody in this server has listened to `{track.name}` by *`{track.artist}`*!"
            )

        items = []
        for index, listener in enumerate(records[:100], start=1):
            user = ctx.guild.get_member(listener.user_id)
            if not user:
                continue

            rank = f"`{index}`"
            if index == 1:
                rank = "👑"

            items.append(
                f"{rank} [{shorten(user.name, 19)}](https://last.fm/user/{listener.username}) has {plural(listener.plays, md='**'):play}"
            )

        return await ctx.paginate(
            items,
            embed=Embed(
                color=ctx.lastfm.color,
                title=f"Top listeners for {shorten(track.name, 12)} by {shorten(track.artist, 12)}",
            ),
            counter=False,
        )
