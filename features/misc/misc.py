import unicodedata
from asyncio import sleep
from base64 import b64decode
from contextlib import suppress
from datetime import datetime
from hashlib import sha1, sha224, sha256, sha384, sha512
from io import BytesIO
from time import perf_counter
from typing import Callable, List, Optional, Tuple, Type

from colorgram import extract
from discord import (
    Color,
    DiscordException,
    Embed,
    File,
    Guild,
    HTTPException,
    Invite,
    Member,
    Message,
    NotFound,
    TextChannel,
    User,
)
from discord.app_commands import ContextMenu
from discord.ext.commands import (
    BucketType,
    Cog,
    Range,
    check,
    command,
    cooldown,
    flag,
    group,
    has_permissions,
    max_concurrency,
    param,
)
from discord.utils import as_chunks, find, format_dt, get, utcnow
from jishaku.codeblocks import Codeblock, codeblock_converter
from jishaku.functools import executor_function
from jishaku.math import mean_stddev
from munch import Munch
from PIL import Image as PILImage
from pyppeteer import launch
from pyppeteer.browser import Browser
from pyppeteer.errors import PageError
from pyppeteer.errors import TimeoutError as PTimeoutError
from shazamio import Serialize as ShazamSerialize
from shazamio import Shazam as ShazamClient
from xxhash import xxh32_hexdigest, xxh64_hexdigest, xxh128_hexdigest
from yarl import URL

import config
from tools.kayo import Kayo
from tools.managers import Context, FlagConverter, Script, logging
from tools.managers.converters import Attachment, Domain, Image
from tools.utilities import human_join, image, plural, sanitize, shorten

log = logging.getLogger(__name__)


class PistonRuntime(Type):
    language: str
    version: str
    aliases: List[str]


class PistonExecute(Type):
    language: str
    run: "PistonOutput"


class PistonOutput(Type):
    stdout: str
    stderr: str
    code: int
    output: str


class ScreenshotFlags(FlagConverter):
    delay: Range[int, 1, 10] = flag(
        description="The amount of seconds to let the page render.",
        default=0,
    )

    full_page: bool = flag(
        description="Whether or not to take a screenshot of the entire page.",
        default=False,
    )


class Miscellaneous(Cog):
    def __init__(self, bot: Kayo):
        self.bot: Kayo = bot
        self.shazamio = ShazamClient()
        self.menus: List[ContextMenu] = [
            # ContextMenu(
            #     name="Shazam",
            #     type=AppCommandType.message,
            #     callback=self.shazam_context,
            # ),
        ]
        for menu in self.menus:
            self.bot.tree.add_command(menu, override=True)

    # async def shazam_context(
    #     self: "Miscellaneous", interaction: Interaction, message: Message
    # ):
    #     attachment = None
    #     for attachment in message.attachments:
    #         if attachment.content_type.startswith(("audio", "video")):
    #             break

    #     if not attachment:
    #         return await interaction.response.send_message(
    #             embed=Embed(
    #                 color=0x2B2D31,
    #                 description="Couldn't find any audio attachments in this message!",
    #             ),
    #             ephemeral=True,
    #         )

    #     await interaction.response.defer(ephemeral=True, thinking=True)

    #     buffer = await attachment.read()
    #     data = await self.shazamio.recognize_song(buffer)
    #     output = ShazamSerialize.full_track(data)

    #     if not (track := output.track):
    #         return await interaction.response.send_message(
    #             embed=Embed(
    #                 color=0x2B2D31,
    #                 description="Couldn't recognize any tracks in this audio attachment!",
    #             ),
    #             ephemeral=True,
    #         )

    #     return await interaction.response.send_message(
    #         embed=Embed(
    #             color=0x2B2D31,
    #             description=(
    #                 f"Found [*`{track.title}`*]({track.youtube_link}) "
    #                 f"by [*`{track.subtitle}`*]({URL(f'https://google.com/search?q={track.subtitle}')})."
    #             ),
    #         ),
    #         ephemeral=True,
    #     )

    @executor_function
    def color_factory(
        self: "Miscellaneous", size: Tuple[int, int], color: Color
    ) -> File:
        """
        Create a panel for a color.
        """

        buffer = BytesIO()

        size = tuple(min(1024, max(1, value)) for value in size)
        colors = color.to_rgb()

        image = PILImage.new("RGB", size=size, color=colors)
        image.save(buffer, "png", optimize=True)

        buffer.seek(0)
        return File(
            fp=buffer,
            filename="color.png",
        )

    @executor_function
    def dominant_color(self: "Miscellaneous", image: BytesIO | bytes) -> Color:
        if not isinstance(image, BytesIO):
            image = BytesIO(image)

        colors = extract(image, 1)
        output = colors[0]
        return Color.from_rgb(*output.rgb)

    @Cog.listener("on_message")
    async def color_search(
        self: "Miscellaneous", message: Message
    ) -> Optional[Message]:
        """
        Automatically show a panel for a color provided.
        """

        if message.author.bot or not message.content:
            return

        size: Tuple[int, int] = (128, 128)
        color: Optional[Color] = None

        arguments = message.content.split()
        if arguments[0] == "##":
            for attachment in message.attachments:
                if (
                    content_type := attachment.content_type
                ) and content_type.startswith("image"):
                    buffer = await attachment.read()
                    color = await self.dominant_color(buffer)

                    break

        else:
            try:
                color = Color.from_str(arguments[0])
            except (ValueError, IndexError):
                return

        if len(arguments) > 1:
            with suppress(ValueError):
                size = tuple(map(int, arguments[1].split("x")))

        if color:
            log.info(
                f"Sending {color} panel for {message.author} ({message.author.id})."
            )

            panel = await self.color_factory(size, color)
            embed = Embed(color=color)
            if message.attachments:
                embed.title = str(color).upper()

            embed.set_image(url="attachment://color.png")

            return await message.reply(
                embed=embed,
                file=panel,
            )

    @Cog.listener("on_user_update")
    async def submit_name(
        self: "Miscellaneous",
        before: User,
        user: User,
    ):
        if before.name == user.name and before.global_name == user.global_name:
            return

        await self.bot.db.execute(
            """
            INSERT INTO metrics.names (user_id, name, pomelo)
            VALUES ($1, $2, $3)
            """,
            user.id,
            before.name
            if user.name != before.name
            else (before.global_name or before.name),
            before.name != user.name,
        )

    @Cog.listener("on_user_update")
    async def submit_avatar(
        self: "Miscellaneous",
        before: User,
        user: User,
    ):
        if before.avatar == user.avatar or not user.avatar:
            return

        channel = self.bot.get_channel(config.avatars_channel)
        if not isinstance(channel, TextChannel):
            return

        try:
            buffer = await user.avatar.read()
            key = xxh128_hexdigest(buffer, seed=1337)
        except (DiscordException, HTTPException, NotFound):
            return log.warn(f"Failed to download asset for {user.name} ({user.id})!")

        message = await channel.send(
            file=File(
                fp=BytesIO(buffer),
                filename=f"{key}."
                + ("png" if not user.avatar.is_animated() else "gif"),
            ),
        )
        await self.bot.db.execute(
            """
            INSERT INTO metrics.avatars (key, user_id, asset)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, key) DO UPDATE
            SET asset = EXCLUDED.asset
            """,
            key,
            user.id,
            message.attachments[0].url,
        )

        log.info(f"Redistributed asset for {user.name} ({user.id}).")

    @command(name="ping")
    @cooldown(1, 5, BucketType.channel)
    async def ping(self: "Miscellaneous", ctx: Context) -> Optional[Message]:
        """
        View the round-trip latency to the Discord API.
        """

        message: Optional[Message] = None
        embed = Embed(title="Round-Trip Latency")

        api_readings: List[float] = []
        websocket_readings: List[float] = []

        for _ in range(5):
            if api_readings:
                embed.description = (
                    ">>> ```bf\n"
                    + "\n".join(
                        f"Trip {index + 1}: {reading * 1000:.2f}ms"
                        for index, reading in enumerate(api_readings)
                    )
                    + "```"
                )

            text = ""

            if api_readings:
                average, stddev = mean_stddev(api_readings)

                text += f"Average: `{average * 1000:.2f}ms` `\N{PLUS-MINUS SIGN}` `{stddev * 1000:.2f}ms`"

            if websocket_readings:
                average = sum(websocket_readings) / len(websocket_readings)

                text += f"\nWebsocket Latency: `{average * 1000:.2f}ms`"
            else:
                text += f"\nWebsocket latency: `{self.bot.latency * 1000:.2f}ms`"

            if message:
                embed = message.embeds[0]
                embed.clear_fields()
                embed.add_field(
                    name="​",
                    value=text,
                )

                before = perf_counter()
                await message.edit(embed=embed)
                after = perf_counter()

                api_readings.append(after - before)
            else:
                embed.add_field(
                    name="​",
                    value=text,
                )

                before = perf_counter()
                message = await ctx.send(embed=embed)
                after = perf_counter()

                api_readings.append(after - before)

            if self.bot.latency > 0.0:
                websocket_readings.append(self.bot.latency)

        if message:
            return message

    @command(
        name="embed",
        aliases=[
            "parse",
            "ce",
        ],
    )
    @has_permissions(manage_messages=True)
    async def embed(
        self: "Miscellaneous",
        ctx: Context,
        *,
        script: Script,
    ) -> Message:
        """
        Compile and send a script.
        """

        return await script.send(ctx)

    @command(
        name="userinfo",
        aliases=[
            "uinfo",
            "ui",
        ],
    )
    async def userinfo(
        self: "Miscellaneous",
        ctx: Context,
        *,
        user: Optional[Member | User],
    ) -> Message:
        """
        View information about a user.
        """

        user = user or ctx.author

        embed = Embed(
            title=(user.name + (" [BOT]" if user.bot else "")),
        )
        embed.set_thumbnail(url=user.display_avatar)

        embed.add_field(
            name="Created",
            value=(
                format_dt(user.created_at, "D")
                + "\n> "
                + format_dt(user.created_at, "R")
            ),
        )

        if isinstance(user, Member):
            embed.add_field(
                name=f"Joined",
                value=(
                    format_dt(user.joined_at, "D")
                    + "\n> "
                    + format_dt(user.joined_at, "R")
                ),
            )

            if user.premium_since:
                embed.add_field(
                    name=f"Boosted",
                    value=(
                        format_dt(user.premium_since, "D")
                        + "\n> "
                        + format_dt(user.premium_since, "R")
                    ),
                )

            if roles := user.roles[1:]:
                embed.add_field(
                    name="Roles",
                    value=", ".join(role.mention for role in list(reversed(roles))[:5])
                    + (f" (+{len(roles) - 5})" if len(roles) > 5 else ""),
                    inline=False,
                )

            if voice := user.voice:
                members = len(voice.channel.members) - 1

                embed.description = f"> {voice.channel.mention} " + (
                    f"with {plural(members):other}" if members else "by themselves"
                )

        records = await self.bot.db.fetch(
            """
            SELECT name
            FROM metrics.names
            WHERE user_id = $1
            AND pomelo IS TRUE
            ORDER BY updated_at DESC
            """,
            user.id,
        )
        if records:
            embed.add_field(
                name="Names",
                value=human_join(
                    [f"`{record['name']}`" for record in records],
                    final="and",
                ),
                inline=False,
            )

        avatars = await self.bot.db.fetch(
            """
            SELECT key
            FROM metrics.avatars
            WHERE user_id = $1
            """,
            user.id,
        )
        if avatars:
            embed.url = f"https://kayo.wock.sh/history/{user.id}"

        return await ctx.send(embed=embed)

    @command(
        name="icon",
        aliases=[
            "servericon",
            "sicon",
            "spfp",
        ],
    )
    async def icon(
        self: "Miscellaneous",
        ctx: Context,
        *,
        invite: Optional[Invite],
    ) -> Message:
        """
        View a server's icon if one is present.
        """

        guild = ctx.guild
        if isinstance(invite, Invite):
            guild: Guild = invite.guild  # type: ignore

        if not guild.icon:
            return await ctx.notice(f"`{guild}` doesn't have an icon present!")

        embed = Embed(url=guild.icon, title=f"{guild}'s icon")
        embed.set_image(url=guild.icon)

        return await ctx.send(embed=embed)

    @command(
        name="serverbanner",
        aliases=["sb"],
    )
    async def serverbanner(
        self: "Miscellaneous",
        ctx: Context,
        *,
        invite: Optional[Invite],
    ) -> Message:
        """
        View a server's banner if one is present.
        """

        guild = ctx.guild
        if isinstance(invite, Invite):
            guild: Guild = invite.guild  # type: ignore

        if not guild.banner:
            return await ctx.notice(f"`{guild}` doesn't have a banner present!")

        embed = Embed(url=guild.banner, title=f"{guild}'s banner")
        embed.set_image(url=guild.banner)

        return await ctx.send(embed=embed)

    @group(
        name="avatar",
        aliases=[
            "pfp",
            "avi",
            "av",
        ],
        invoke_without_command=True,
    )
    async def avatar(
        self: "Miscellaneous",
        ctx: Context,
        *,
        user: Optional[Member | User],
    ) -> Message:
        """
        View a user's avatar.
        """

        user = user or ctx.author

        embed = Embed(
            url=user.display_avatar,
            title=("Your avatar" if user == ctx.author else f"{user.name}'s avatar"),
        )
        embed.set_image(url=user.display_avatar)

        return await ctx.send(embed=embed)

    @avatar.group(
        name="history",
        aliases=["h"],
        invoke_without_command=True,
    )
    async def avatar_history(
        self: "Miscellaneous",
        ctx: Context,
        *,
        user: Optional[Member | User],
    ) -> Message:
        """
        View a user's previous avatars.
        """

        user = user or ctx.author
        avatars = await self.bot.db.fetch(
            """
            SELECT asset
            FROM metrics.avatars
            WHERE user_id = $1
            ORDER BY updated_at DESC
            """,
            user.id,
        )
        if not avatars:
            return await ctx.notice(f"I haven't tracked any avatars for `{user}`!")

        async with ctx.typing():
            collage = await image.collage(
                self.bot.session, [row["asset"] for row in avatars[:35]]
            )

        embed = Embed(
            title=("Your" if user == ctx.author else f"{user.name}'s")
            + " avatar history",
            description=(
                f"Displaying `{len(avatars[:35])}` of {plural(avatars, md='`'):avatar}."
                f"\n> View the full list including GIFs [__HERE__](https://kayo.wock.sh/history/{user.id})."
            ),
        )
        embed.set_image(url="attachment://collage.png")

        return await ctx.send(
            embed=embed,
            file=collage,
        )

    @avatar_history.command(
        name="wipe",
        aliases=[
            "sweep",
            "clear",
            "remove",
        ],
    )
    async def avatar_history_wipe(self: "Miscellaneous", ctx: Context) -> Message:
        """
        Remove all of your tracked avatars.
        """

        await self.bot.db.execute(
            """
            DELETE FROM metrics.avatars
            WHERE user_id = $1
            """,
            ctx.author.id,
        )

        return await ctx.approve("Successfully wiped your avatar history.")

    @avatar_history.command(
        name="metrics",
        aliases=[
            "statistics",
            "stats",
        ],
    )
    @cooldown(1, 30, BucketType.channel)
    async def avatar_history_metrics(self: "Miscellaneous", ctx: Context) -> Message:
        """
        View statistics about avatar tracking.
        """

        async with ctx.typing():
            data = await self.bot.db.fetchrow(
                """
                SELECT
                    COUNT(*) AS analyzed,
                    MIN(updated_at) AS first_update,
                    pg_size_pretty(pg_total_relation_size('metrics.avatars')) AS storage
                FROM metrics.avatars
                """
            )
            overall_updates = "\n".join(
                [
                    f"[__`{user}`__](https://kayo.wock.sh/history/{row['user_id']}) has {plural(row['count'], md='**'):avatar}"
                    for row in await self.bot.db.fetch(
                        """
                    SELECT user_id, COUNT(user_id)
                    FROM metrics.avatars
                    GROUP BY user_id
                    ORDER BY COUNT(user_id)
                    DESC LIMIT 3
                    """
                    )
                    if (user := (self.bot.get_user(row["user_id"]) or row["user_id"]))
                ]
            )

        embed = Embed(
            title="Avatar Metrics",
            description=f"Tracked {plural(data['analyzed'], md='`'):avatar} so far.",
        )
        embed.set_footer(text="Tracking since")
        embed.timestamp = data.get("first_update")

        embed.add_field(
            name="Overall Updates",
            value=">>> " + overall_updates,
            inline=False,
        )

        return await ctx.send(embed=embed)

    @command(
        name="banner",
        aliases=["userbanner", "ub"],
    )
    async def banner(
        self: "Miscellaneous",
        ctx: Context,
        *,
        user: Optional[Member | User],
    ) -> Message:
        """
        View a user's banner if one is present.
        """

        if not isinstance(user, User):
            user_id = user.id if user else ctx.author.id
            user = await self.bot.fetch_user(user_id)

        if not user.banner:
            return await ctx.notice(
                "You don't have a banner present!"
                if user == ctx.author
                else f"`{user}` doesn't have a banner present!"
            )

        embed = Embed(
            url=user.banner,
            title=("Your banner" if user == ctx.author else f"{user.name}'s banner"),
        )
        embed.set_image(url=user.banner)

        return await ctx.send(embed=embed)

    @command(
        name="namehistory",
        aliases=["names", "nh"],
    )
    async def name_history(
        self: "Miscellaneous",
        ctx: Context,
        *,
        user: Member | User,
    ) -> Message:
        """
        View a user's previous names.
        """

        records = await self.bot.db.fetch(
            """
            SELECT name, pomelo, updated_at
            FROM metrics.names
            WHERE user_id = $1
            ORDER BY updated_at DESC
            """,
            user.id,
        )
        if not records:
            return await ctx.notice(f"I haven't tracked any names for `{user}`!")

        return await ctx.paginate(
            [
                (
                    f"\"__{record['pomelo'] and '@' or ''}{record['name']}__\" "
                    f"({format_dt(record['updated_at'], 'R')})"
                )
                for record in records
            ],
            embed=Embed(title="Name History"),
        )

    @group(name="hash")
    async def _hash(self: "Miscellaneous", ctx: Context) -> Optional[Message]:
        """
        Hash a string with a given algorithm.
        """

        if ctx.invoked_subcommand is None:
            hash_methods = human_join(
                [
                    f"`{command.name}`"
                    for command in sorted(
                        self._hash.commands,
                        key=lambda command: command.name,
                        reverse=True,
                    )
                ]
            )

            return await ctx.notice(
                f"Please specify a valid hash algorithm to use!" f"\n> {hash_methods}"
            )

    @_hash.command(name="xxh32")
    async def _hash_xxh32(self: "Miscellaneous", ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the XXH32 algorithm.
        """

        hashed = xxh32_hexdigest(text)

        embed = Embed(
            title="XXH32 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="xxh64")
    async def _hash_xxh64(self: "Miscellaneous", ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the XXH64 algorithm.
        """

        hashed = xxh64_hexdigest(text)

        embed = Embed(
            title="XXH64 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="xxh128")
    async def _hash_xxh128(
        self: "Miscellaneous", ctx: Context, *, text: str
    ) -> Message:
        """
        Hash a string with the XXH128 algorithm.
        """

        hashed = xxh128_hexdigest(text)

        embed = Embed(
            title="XXH128 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha1")
    async def _hash_sha1(self: "Miscellaneous", ctx: Context, *, text: str) -> Message:
        """
        Hash a string with the SHA1 algorithm.
        """

        hashed = sha1(text.encode()).hexdigest()

        embed = Embed(
            title="SHA1 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha224")
    async def _hash_sha224(
        self: "Miscellaneous", ctx: Context, *, text: str
    ) -> Message:
        """
        Hash a string with the SHA224 algorithm.
        """

        hashed = sha224(text.encode()).hexdigest()

        embed = Embed(
            title="SHA224 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha256")
    async def _hash_sha256(
        self: "Miscellaneous", ctx: Context, *, text: str
    ) -> Message:
        """
        Hash a string with the SHA256 algorithm.
        """

        hashed = sha256(text.encode()).hexdigest()

        embed = Embed(
            title="SHA256 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha384")
    async def _hash_sha384(
        self: "Miscellaneous", ctx: Context, *, text: str
    ) -> Message:
        """
        Hash a string with the SHA384 algorithm.
        """

        hashed = sha384(text.encode()).hexdigest()

        embed = Embed(
            title="SHA384 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @_hash.command(name="sha512")
    async def _hash_sha512(
        self: "Miscellaneous", ctx: Context, *, text: str
    ) -> Message:
        """
        Hash a string with the SHA512 algorithm.
        """

        hashed = sha512(text.encode()).hexdigest()

        embed = Embed(
            title="SHA512 Hash",
            description=(
                f"> **Original**"
                f"\n```{text}```"
                f"\n> **Hashed**"
                f"\n```{hashed}```"
            ),
        )
        return await ctx.send(embed=embed)

    @group(
        name="compile",
        aliases=[
            "build",
            "eval",
            "run",
        ],
        invoke_without_command=True,
    )
    async def compile(
        self: "Miscellaneous",
        ctx: Context,
        *,
        code: Codeblock = param(
            converter=codeblock_converter,
            description="The code to compile.",
        ),
    ) -> Message:
        """
        Evaluate code through a private Piston instance.

        The default runtime language is python, however you can change this by
        wrapping your code inside of a code block with the language you want to use.
        You can also view a list available languages with `compile runtimes` command.

        > Below is a **Hello world** example using `go`.
        ```go
        package main
        import \"fmt\"

        func main() {
            fmt.Print(\"Hello world\")
        }```
        """

        async with ctx.typing():
            language = code.language or "python"

            runtimes: List[Munch] = await self.bot.session.request(
                "https://emkc.org/api/v2/piston/runtimes",
            )
            runtime: Optional[Munch] = find(
                lambda runtime: (
                    language.lower() == runtime.language
                    or language.lower() in runtime.aliases
                ),
                runtimes,
            )
            if not runtime:
                return await ctx.notice(
                    f"Couldn't find a runtime for `{code.language}`!"
                )

            data: PistonExecute = await self.bot.session.request(
                "POST",
                "https://emkc.org/api/v2/piston/execute",
                json={
                    "language": runtime.language,
                    "version": runtime.version,
                    "files": [
                        {
                            "name": xxh64_hexdigest(code.content),
                            "content": code.content,
                        },
                    ],
                },
            )

        embeds = []
        for chunk in as_chunks(data.run.output, 2000):
            chunk = "".join(chunk)

            embed = Embed(
                description=(
                    f"> Compiled `{data.language}` code.\n"
                    f"```{runtime.language}\n{chunk}```"
                ),
            )
            embeds.append(embed)

        if not embeds:
            return await ctx.notice("No output was returned.")

        return await ctx.paginate(embeds)

    @compile.command(
        name="runtimes",
        aliases=[
            "languages",
            "langs",
        ],
    )
    async def compile_runtimes(self: "Miscellaneous", ctx: Context) -> Message:
        """
        View all available runtimes.
        """

        runtimes: List[PistonRuntime] = await self.bot.session.request(
            "https://emkc.org/api/v2/piston/runtimes",
        )

        return await ctx.paginate(
            [
                (
                    f"**{runtime.language}** (`v{runtime.version}`)"
                    + (f" | *{', '.join(runtime.aliases)}*" if runtime.aliases else "")
                )
                for runtime in runtimes
            ],
            embed=Embed(title="Available Runtimes"),
        )

    @command(
        name="charinfo",
        aliases=["char"],
    )
    async def charinfo(
        self: "Miscellaneous", ctx: Context, *, characters: str
    ) -> Message:
        """
        View information about unicode characters.
        """

        def to_string(char: str):
            digit = f"{ord(char):x}"
            name = unicodedata.name(char, "Name not found.")

            return f"[`\\U{digit:>08}`](http://www.fileformat.info/info/unicode/char/{digit}): {name}"

        return await ctx.paginate(
            list(map(to_string, characters)),
            embed=Embed(title="Character Information"),
            max_results=5,
            counter=False,
        )

    @command(
        name="sauce",
        usage="<image>",
        aliases=["rimg"],
    )
    @cooldown(1, 5, BucketType.user)
    async def sauce(
        self: "Miscellaneous",
        ctx: Context,
        image: Image = param(
            default=Image.fallback,
            description="The image to search.",
        ),
    ) -> Message:
        """
        Search for the source of an image.
        """

        async with ctx.typing():
            data = await self.bot.session.request(
                "POST",
                "https://tineye.com/result_json/",
                params={
                    "sort": "score",
                    "order": "desc",
                },
                data={
                    "image": image.fp,
                },
            )
            if not data.matches:
                return await ctx.notice(
                    f"Couldn't find any matches for [`{data.query.hash}`]({image.url})!"
                )

        embed = Embed(
            title="Reverse Image Lookup",
            description=(
                f"Found {plural(data.num_matches, md='`'):match|matches} for [`{image.filename}`]({image.url})."
            ),
        )
        embed.set_thumbnail(url=image.url)

        for match in data.matches[:4]:
            backlink = match.backlinks[0]

            embed.add_field(
                name=match.domain,
                value=f"[`{shorten(backlink.backlink.replace('https://', '').replace('http://', ''))}`]({backlink.url})",
                inline=False,
            )

        return await ctx.send(embed=embed)

    @command(
        name="shazam",
        usage="<attachment>",
        aliases=[
            "recognize",
            "find",
        ],
    )
    @max_concurrency(1, wait=True)
    @cooldown(1, 5, BucketType.user)
    async def shazam(
        self: "Miscellaneous",
        ctx: Context,
        attachment: Attachment = param(
            default=Attachment.fallback,
            description="The attachment to read.",
        ),
    ) -> Message:
        """
        Recognizes songs from an attachment.
        """

        async with ctx.typing():
            data = await self.shazamio.recognize_song(attachment.fp)
            output = ShazamSerialize.full_track(data)

        if not (track := output.track):
            return await ctx.notice(
                f"Couldn't recognize any tracks from [{attachment.filename}]({attachment.url})!"
            )

        return await ctx.approve(
            f"Found [*`{track.title}`*]({track.youtube_link}) "
            f"by [*`{track.subtitle}`*]({URL(f'https://google.com/search?q={track.subtitle}')}).",
            reference=ctx.message,
        )

    @command(
        name="screenshot",
        aliases=["ss"],
    )
    @max_concurrency(1, wait=True)
    @cooldown(1, 4, BucketType.user)
    async def screenshot(
        self: "Miscellaneous",
        ctx: Context,
        url: URL = param(
            converter=Domain,
            description="The URL to screenshot.",
        ),
        *,
        flags: ScreenshotFlags,
    ) -> Message:
        """
        Takes a screenshot of a website.
        """

        async with ctx.typing():
            page = await self.browser.newPage()
            await page.setViewport(
                {
                    "width": 1920,
                    "height": 1080,
                }
            )
            await page.setUserAgent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/111.0"
            )

            try:
                await page.goto(
                    url=str(url),
                    options={
                        "timeout": 15e3,
                        "wait_until": "networkidle0",
                    },
                )
            except (PageError, PTimeoutError):
                await page.close()
                return await ctx.notice(f"Host [`{url.host}`]({url}) is not reachable!")

            if isinstance(ctx.channel, TextChannel):
                await Domain().convert(ctx, page.url)

            await sleep(flags.delay)
            buffer: bytes = await page.screenshot(
                options={
                    "fullPage": flags.full_page,
                },
            )  # type: ignore
            await page.close()

        embed = Embed(description=f"> [*`{url.host}`*]({url})")
        embed.set_image(url="attachment://screenshot.png")
        embed.set_footer(
            text=(
                f"Requested by {ctx.author}"
                + (f" ∙ {delay}s delay" if (delay := flags.delay) else "")
                + (" ∙ Full page" if flags.full_page else "")
            ),
        )

        return await ctx.send(
            embed=embed,
            file=File(
                BytesIO(buffer),
                filename="screenshot.png",
            ),
        )

    @command(
        name="image",
        aliases=[
            "img",
            "im",
            "i",
        ],
    )
    async def google_images(
        self: "Miscellaneous",
        ctx: Context,
        *,
        query: str,
    ) -> Message:
        """
        Search google images.
        """

        async with ctx.typing():
            results: List[Munch] = await self.bot.session.request(
                "https://notsobot.com/api/search/google/images",
                params={"query": query},
            )
            if not results:
                return await ctx.notice(f"No image results found for query `{query}`!")

        embeds = []
        for result in results:
            embed = Embed(
                url=result.url,
                title=f"{result.header} ({result.footer})",
                description=shorten(result.description, 38),
            )

            embed.set_image(url=result.image.url)
            embeds.append(embed)

        return await ctx.paginate(embeds)

    @command(
        name="minecraft",
        aliases=["craft"],
    )
    async def minecraft(self: "Miscellaneous", ctx: Context, server_ip: str) -> Message:
        """
        View Minecraft server information.
        """

        async with ctx.typing():
            data: Munch = await self.bot.session.request(
                f"https://api.mcsrvstat.us/2/{sanitize(server_ip)}"
            )
            if not data.online:
                return await ctx.notice(f"Server `{server_ip}` is not reachable!")

        embed = Embed(
            description=(
                f"{data.version}\n>>> ```bf\n"
                + "\n".join(line.strip() for line in data.motd.clean)
                + "```"
            ),
        )
        embed.set_author(
            name=f"{data.hostname} ({data.ip})",
            icon_url=("attachment://icon.png" if data.icon else None),
        )

        embed.set_footer(text=f"{data.players.online:,}/{data.players.max:,} players")

        if data.icon:
            buffer = b64decode(data.icon.split(",")[1])
            return await ctx.send(
                embed=embed,
                file=File(
                    BytesIO(buffer),
                    filename="icon.png",
                ),
            )

        return await ctx.send(embed=embed)

    @group(
        name="color",
        aliases=[
            "colour",
            "cr",
            "br",
        ],
        invoke_without_command=True,
    )
    @check(
        lambda ctx: bool(ctx.author.premium_since)
        and ctx.guild.id == 1128849931269062688
    )
    async def color(self: "Miscellaneous", ctx: Context, color: Color) -> Message:
        """
        Create your own unique color role.

        __You must be a booster to use this feature.__
        """

        key = f"color:{xxh32_hexdigest(str(ctx.author.id))}"
        role = get(ctx.guild.roles, name=key)

        if role:
            await role.edit(color=color, reason=ctx.author.name)
        else:
            role = await ctx.guild.create_role(
                name=key,
                color=color,
                reason=ctx.author.name,
            )
            await ctx.guild.edit_role_positions(
                positions={
                    role: ctx.guild.me.top_role.position - 1,
                },
                reason=ctx.author.name,
            )

        if role not in ctx.author.roles:
            await ctx.author.add_roles(role, reason=ctx.author.name)

        return await ctx.approve(f"Successfully set your color to `{color}`.")

    @color.command(
        name="remove",
        aliases=[
            "delete",
            "del",
            "rm",
        ],
    )
    @check(
        lambda ctx: bool(ctx.author.premium_since)
        and ctx.guild.id == 1128849931269062688
    )
    async def color_remove(self: "Miscellaneous", ctx: Context) -> Message:
        """
        Delete your color role.
        """

        key = f"color:{xxh32_hexdigest(str(ctx.author.id))}"
        role = get(ctx.guild.roles, name=key)

        if not role:
            return await ctx.notice("You don't have a color role!")

        await role.delete(reason=ctx.author.name)
        return await ctx.approve("Successfully removed your color role.")

    @color.command(
        name="clean",
        aliases=["clear"],
    )
    @has_permissions(manage_roles=True)
    @check(lambda ctx: ctx.guild.id == 1128849931269062688)
    async def color_clean(self: "Miscellaneous", ctx: Context) -> Message:
        """
        Remove all color roles.
        """

        if tasks := [
            self.bot.ioloop.add_callback(role.delete)
            for role in ctx.guild.roles
            if role.name.startswith("color:")
        ]:
            return await ctx.approve(
                f"Successfully removed {plural(tasks, md='`'):color role}."
            )
        else:
            return await ctx.notice("No color roles exist!")

    @group(
        name="fortnite",
        aliases=["fort", "fn"],
    )
    async def fortnite(self: "Miscellaneous", ctx: Context) -> Message:
        """
        View Fortnite related information.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @fortnite.command(
        name="shop",
        aliases=["itemshop", "store"],
    )
    async def fortnite_shop(self: "Miscellaneous", ctx: Context) -> Message:
        """
        View the current Fortnite item shop.
        """

        embed = Embed(title="Fortnite Item Shop")
        embed.set_image(
            url=f"https://bot.fnbr.co/shop-image/fnbr-shop-{utcnow().strftime('%-d-%-m-%Y')}.png"
        )

        return await ctx.send(embed=embed)

    @fortnite.command(
        name="cosmetic",
        aliases=["lookup", "find"],
    )
    async def fortnite_cosmetic(
        self: "Miscellaneous", ctx: Context, *, cosmetic: str
    ) -> Message:
        """
        Display information about a Fortnite cosmetic.
        """

        result: Munch = await self.bot.session.request(
            "GET",
            f"https://fortnite-api.com/v2/cosmetics/br/search",
            params=dict(
                name=cosmetic,
                matchMethod="contains",
            ),
            headers=dict(
                Authorization=config.Authorization.Fortnite,
            ),
        )
        cosmetic = result.data

        timestamp: Callable[[str], str] = lambda date: (
            format_dt(
                datetime.fromisoformat(
                    date.replace("Z", "+00:00")
                    .replace("T", " ")
                    .split(".")[0]
                    .replace(" ", "T")
                ),
                style="D",
            )
            + " ("
            + format_dt(
                datetime.fromisoformat(
                    date.replace("Z", "+00:00")
                    .replace("T", " ")
                    .split(".")[0]
                    .replace(" ", "T")
                ),
                style="R",
            )
            + ")"
        )

        embed = Embed(
            url=f"https://fnbr.co/{cosmetic.type.value}/{cosmetic.name.replace(' ', '-')}",
            title=cosmetic.name,
            description=f"{cosmetic.description}\n> {cosmetic.introduction.text}",
        )
        embed.set_thumbnail(url=cosmetic.images.icon)

        embed.add_field(
            name="Releases",
            value="\n".join(
                timestamp(date) for date in list(reversed(cosmetic.shopHistory))[:5]
            )
            if cosmetic.shopHistory
            else timestamp(cosmetic.added),
        )

        return await ctx.send(embed=embed)

    # @group(
    #     name="highlight",
    #     aliases=["hl"],
    # )
    # async def highlight(self: "Miscellaneous", ctx: Context) -> Message:
    #     """
    #     Receive notifications for keywords.
    #     """

    #     if ctx.invoked_subcommand is None:
    #         return await ctx.send_help(ctx.command)

    # @highlight.command(
    #     name="add",
    #     aliases=["create"],
    # )
    # async def highlight_add(
    #     self: "Miscellaneous",
    #     ctx: Context,
    #     *,
    #     keyword: str,
    # ) -> Message:
    #     """
    #     Add a new highlight.
    #     """

    #     try:
    #         await ctx.author.send()
    #     except HTTPException as exc:
    #         if exc.code == 50007:
    #             return await ctx.notice("You must enable DMs to use this command!")

    #     if len(keyword) < 2:
    #         return await ctx.notice("Keywords must be at least 2 characters long!")
    #     elif len(keyword) > 20:
    #         return await ctx.notice("Keywords cannot be longer than 20 characters!")

    #     try:
    #         await self.bot.db.execute(
    #             """
    #             INSERT INTO highlight.words
    #             VALUES ($1, $2, $3)
    #             """,
    #             ctx.guild.id,
    #             ctx.author.id,
    #             keyword,
    #         )
    #     except UniqueViolationError:
    #         return await ctx.notice(f"You're already receiving notifications for `{keyword}`!")
    #     else:
    #         return await ctx.approve(f"You'll now receive notifications for `{keyword}`.")

    # @highlight.command(
    #     name="remove",
    #     aliases=[
    #         "delete",
    #         "del",
    #         "rm",
    #     ],
    # )
    # async def highlight_remove(
    #     self: "Miscellaneous",
    #     ctx: Context,
    #     *,
    #     keyword: str,
    # ) -> Message:
    #     """
    #     Remove an existing highlight.
    #     """

    #     result = await self.bot.db.execute(
    #         """
    #         DELETE FROM highlight.words
    #         WHERE guild_id = $1
    #         AND user_id = $2
    #         AND keyword = $3
    #         """,
    #         ctx.guild.id,
    #         ctx.author.id,
    #         keyword,
    #     )
    #     if result == "DELETE 0":
    #         return await ctx.notice(f"You're not receiving notifications for `{keyword}`!")

    #     return await ctx.approve(f"You'll no longer receive notifications for `{keyword}`.")

    # @highlight.command(
    #     name="block",
    #     aliases=["ignore"],
    # )
    # async def highlight_block(
    #     self: "Miscellaneous",
    #     ctx: Context,
    #     *,
    #     entity: Member | TextChannel | CategoryChannel,
    # ) -> Message:
    #     """
    #     Block an entity from triggering highlights.
    #     """

    #     try:
    #         await self.bot.db.execute(
    #             """
    #             INSERT INTO highlight.blacklist
    #             VALUES ($1, $2)
    #             """,
    #             ctx.author.id,
    #             entity.id,
    #         )
    #     except UniqueViolationError:
    #         return await ctx.notice(f"You've already blocked {entity.mention}!")
    #     else:
    #         return await ctx.approve(f"You'll no longer receive notifications from {entity.mention}.")

    # @highlight.command(
    #     name="unblock",
    #     aliases=["unignore"],
    # )
    # async def highlight_unblock(
    #     self: "Miscellaneous",
    #     ctx: Context,
    #     *,
    #     entity: Member | TextChannel | CategoryChannel,
    # ) -> Message:
    #     """
    #     Allow an entity to trigger highlights.
    #     """

    #     result = await self.bot.db.execute(
    #         """
    #         DELETE FROM highlight.blacklist
    #         WHERE user_id = $1
    #         AND entity_id = $2
    #         """,
    #         ctx.author.id,
    #         entity.id,
    #     )
    #     if result == "DELETE 0":
    #         return await ctx.notice(f"You haven't blocked {entity.mention}!")

    #     return await ctx.approve(f"You'll now receive notifications from {entity.mention}.")
