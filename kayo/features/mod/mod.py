import re
from base64 import b64encode
from datetime import datetime
from enum import Enum
from io import BytesIO
from mimetypes import guess_type
from random import randint
from typing import Dict, List, Optional, Union
from zipfile import ZipFile

from discord import (
    Asset,
    Attachment,
    Embed,
    Emoji,
    File,
    Forbidden,
    HTTPException,
    Member,
    Message,
    PartialEmoji,
    TextChannel,
    User,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    CooldownMapping,
    Range,
    command,
    cooldown,
    group,
    has_permissions,
)
from discord.http import Route
from discord.utils import format_dt
from jishaku.functools import executor_function
from pydub import AudioSegment
from pydub.silence import split_on_silence as silence
from typing_extensions import Self, Type
from xxhash import xxh64_hexdigest

from tools.kayo import Kayo
from tools.managers import Context, Sound, logging
from tools.managers.database import Record
from tools.utilities import Error, plural

log = logging.getLogger(__name__)


class Action(Enum):
    UNKNOWN = 0
    KICK = 1
    BAN = 2
    HACKBAN = 3
    SOFTBAN = 4
    UNBAN = 5
    TIMEOUT = 6
    UNTIMEOUT = 7

    def __int__(self: "Action") -> int:
        return self.value

    def __str__(self: "Action") -> str:
        if self == Action.KICK:
            return "Kicked"

        elif self == Action.BAN:
            return "Banned"

        elif self == Action.HACKBAN:
            return "Hack Banned"

        elif self == Action.SOFTBAN:
            return "Soft Banned"

        elif self == Action.UNBAN:
            return "Unbanned"

        elif self == Action.TIMEOUT:
            return "Timed Out"

        elif self == Action.UNTIMEOUT:
            return "Timeout Lifted"

        else:
            return "Unknown"


class Case:
    def __init__(self: "Case", case: Record, bot: Kayo):
        self.bot: Kayo = bot
        self.id: int = case.id
        self.guild_id: int = case.guild_id
        self.target_id: int = case.target_id
        self.moderator_id: int = case.moderator_id
        self.message_id: Optional[int] = case.message_id
        self.reason: str = case.reason
        self.action: Action = Action(case.action)
        self.action_expiration: Optional[datetime] = case.action_expiration
        self.action_processed: bool = case.action_processed
        self.created_at: datetime = case.created_at
        self.updated_at: Optional[datetime] = case.updated_at

    async def channel(self: "Case") -> Optional[TextChannel]:
        if channel_id := await self.bot.db.fetchval(
            """
                SELECT mod_log_channel_id
                FROM settings
                WHERE guild_id = $1
                """,
            self.guild_id,
        ):
            return self.bot.get_channel(channel_id)  # type: ignore

    async def embed(self: "Case") -> Embed:
        target = self.bot.get_user(self.target_id) or await self.bot.fetch_user(
            self.target_id
        )
        moderator = self.bot.get_user(self.moderator_id) or await self.bot.fetch_user(
            self.moderator_id
        )

        embed = Embed(color=0x2B2D31)
        embed.set_author(
            name=f"{moderator} ({moderator.id})",
            icon_url=moderator.display_avatar,
        )

        information = (
            f"{format_dt(self.created_at)} ({format_dt(self.created_at, 'R')})\n>>> "
            f"**Member:** {target} (`{target.id}`)\n"
        )

        if self.action_expiration:
            information += f"**Expiration:** {format_dt(self.action_expiration)} ({format_dt(self.action_expiration, 'R')})\n"

        if self.updated_at:
            information += f"**Updated:** {format_dt(self.updated_at)} ({format_dt(self.updated_at, 'R')})\n"

        if self.reason:
            information += f"**Reason:** {self.reason}\n"

        embed.add_field(
            name=f"Case #{self.id} | {self.action}",
            value=information,
        )

        return embed

    async def send(self: "Case") -> Optional[Message]:
        channel = await self.channel()
        if not channel:
            return

        embed = await self.embed()
        try:
            message = await channel.send(embed=embed)
        except Forbidden:
            await self.bot.db.execute(
                """
                UPDATE settings
                SET mod_log_channel_id = NULL
                WHERE guild_id = $1
                """,
                self.guild_id,
            )
            return
        except HTTPException:
            return

        await self.bot.db.execute(
            """
            UPDATE cases
            SET message_id = $3
            WHERE guild_id = $1
            AND id = $2
            """,
            self.guild_id,
            self.id,
            message.id,
        )
        return message

    @classmethod
    async def convert(cls: Type[Self], ctx: Context, argument: str) -> Self:
        if not (match := re.match(r"^#?(\d+)$", argument)):
            raise Error("You must provide a valid case ID, example: `#27`.")

        case_id = int(match[1])
        case = await ctx.bot.db.fetchrow(
            """
            SELECT *
            FROM cases
            WHERE guild_id = $1
            AND id = $2
            """,
            ctx.guild.id,
            case_id,
        )
        if not case:
            raise Error(f"Case ID `#{case_id}` does not exist!")

        return cls(case, ctx.bot)

    def __repr__(self) -> str:
        return (
            f"<KayoCase id={self.id} action={self.action.name} reason={self.reason!r}>"
        )


class Moderation(Cog):
    def __init__(self, bot: Kayo):
        self.bot: Kayo = bot
        self.ladder_control = CooldownMapping.from_cooldown(3, 3, BucketType.member)

    async def insert_case(
        self: "Moderation",
        ctx: Context,
        target: Member | User,
        reason: str = "No reason provided",
        action: Action = Action.UNKNOWN,
        action_expriation: Optional[datetime] = None,
        action_processed: bool = True,
    ) -> Case:
        case = await self.bot.db.fetchrow(
            """
            INSERT INTO cases (
                id,
                guild_id,
                target_id,
                moderator_id,
                reason,   
                action,
                action_expiration,
                action_processed
            )
            VALUES (NEXT_CASE($1), $1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            ctx.guild.id,
            target.id,
            ctx.author.id,
            reason,
            action,
            action_expriation,
            action_processed,
        )
        case = Case(case, self.bot)

        if case.channel:
            self.bot.ioloop.add_callback(case.send)

        return case

    async def collect_hashes(
        self: "Moderation",
        assets: List[Union[Asset, Emoji]],
    ) -> Dict[str, List[Union[Asset, Emoji]]]:
        seed = randint(0, 1000)
        hashes: Dict[str, List[Union[Asset, Emoji]]] = {}

        for asset in assets:
            buffer = await asset.read()
            key = xxh64_hexdigest(buffer, seed=seed)

            if key not in hashes:
                hashes[key] = []

            hashes[key].append(asset)

        return hashes

    # @Cog.listener("on_message")
    # async def ladder_flood(self: "Moderation", message: Message) -> None:
    #     """
    #     Automatically mute retards which ladder type.
    #     """

    #     if (
    #         message.author.bot
    #         or not message.content
    #         or not isinstance(message.author, Member)
    #     ):
    #         return

    #     if len(message.content) >= 6 or message.author.premium_since:
    #         return

    #     bucket = self.ladder_control.get_bucket(message)
    #     if bucket and bucket.update_rate_limit():
    #         await message.author.timeout(
    #             timedelta(minutes=5),
    #             reason=f"User caught ladder typing in #{message.channel}",
    #         )

    #         await message.channel.send(
    #             f"Cleaning up after that retard {message.author}...",
    #         )
    #         self.bot.ioloop.add_callback(
    #             message.channel.purge,  # type: ignore
    #             check=lambda m: m.author == message.author,
    #         )

    @command(
        name="kick",
        aliases=["boot"],
    )
    @has_permissions(kick_members=True)
    async def kick(
        self: "Moderation",
        ctx: Context,
        member: Member,
        *,
        reason: str = "No reason provided",
    ) -> None:
        """
        Kick a member from the server.
        """

        await member.kick(reason=f"{ctx.author} / {reason}")
        await self.insert_case(
            ctx,
            target=member,
            reason=reason,
            action=Action.KICK,
        )

        return await ctx.add_check()

    @command(
        name="ban",
        aliases=["deport"],
    )
    @has_permissions(ban_members=True)
    async def ban(
        self: "Moderation",
        ctx: Context,
        user: Member | User,
        *,
        reason: str = "No reason provided",
    ) -> None:
        """
        Ban a user from the server.
        """

        await ctx.guild.ban(user, reason=f"{ctx.author} / {reason}")
        await self.insert_case(
            ctx,
            target=user,
            reason=reason,
            action=Action.BAN,
        )

        return await ctx.add_check()

    @group(
        name="case",
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    async def case(self: "Moderation", ctx: Context, case: Case) -> Message:
        """
        View information about a case ID.
        """

        embed = await case.embed()
        return await ctx.reply(embed=embed)

    @command(
        name="steal",
        aliases=["add"],
    )
    @has_permissions(manage_emojis=True)
    async def steal(
        self: "Moderation",
        ctx: Context,
        emoji: Emoji | PartialEmoji,
        *,
        name: Optional[str],
    ) -> Message:
        """
        Adds an emoji to the server.
        """

        if isinstance(emoji, Emoji) and emoji.guild_id == ctx.guild.id:
            return await ctx.notice("That emoji is already in this server!")

        if name:
            if len(name) < 2:
                return await ctx.notice(
                    "Emoji names must be at least 2 characters long."
                )

            name = name[:32].replace(" ", "_")

        if len(ctx.guild.emojis) == ctx.guild.emoji_limit:
            return await ctx.notice("The server is at the maximum amount of emojis!")

        buffer: bytes = await self.bot.session.request(emoji.url)
        try:
            emoji = await ctx.guild.create_custom_emoji(
                name=name or emoji.name,
                image=buffer,
                reason=ctx.author.name,
            )
        except HTTPException:
            return await ctx.notice(
                f"Failed to add [`{name or emoji.name}`]({emoji.url}) to the server!"
            )

        return await ctx.approve(
            f"Added [`{name or emoji.name}`]({emoji.url}) to the server."
        )

    @group(
        name="emoji",
        aliases=["emote"],
    )
    @has_permissions(manage_emojis=True)
    async def emoji(self: "Moderation", ctx: Context) -> Message:
        """
        Various emoji related commands.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @emoji.command(
        name="rename",
        aliases=["name"],
    )
    @has_permissions(manage_emojis=True)
    async def emoji_rename(
        self: "Moderation",
        ctx: Context,
        emoji: Emoji,
        *,
        name: str,
    ) -> Message:
        """
        Rename an emoji in the server.
        """

        if emoji.guild_id != ctx.guild.id:
            return await ctx.notice("That emoji is not in this server!")

        if len(name) < 2:
            return await ctx.notice("Emoji names must be at least 2 characters long.")

        name = name[:32].replace(" ", "_")
        await emoji.edit(name=name, reason=ctx.author.name)
        return await ctx.approve(f"Renamed the emoji to `{name}`.")

    @emoji.group(
        name="delete",
        aliases=[
            "remove",
            "del",
        ],
        invoke_without_command=True,
    )
    @has_permissions(manage_emojis=True)
    async def emoji_delete(
        self: "Moderation",
        ctx: Context,
        emoji: Emoji,
    ) -> Message:
        """
        Delete an emoji from the server.
        """

        if emoji.guild_id != ctx.guild.id:
            return await ctx.notice("That emoji is not in this server!")

        await emoji.delete(reason=ctx.author.name)
        return await ctx.approve(f"Deleted the emoji `{emoji.name}`.")

    @emoji_delete.command(
        name="duplicates",
        aliases=[
            "dupes",
            "dups",
        ],
    )
    @has_permissions(manage_emojis=True)
    @cooldown(1, 60, BucketType.guild)
    async def emoji_delete_duplicates(
        self: "Moderation",
        ctx: Context,
    ) -> Message:
        """
        Delete duplicate emojis from the server.
        """

        await ctx.neutral("Determining hashes for all emojis...")

        async with ctx.typing():
            hashes = await self.collect_hashes(ctx.guild.emojis)
            duplicates: Dict[str, List[Emoji]] = {}
            for key, emojis in hashes.items():
                if len(emojis) > 1:
                    duplicates[key] = emojis

            if not duplicates:
                return await ctx.notice("No duplicate emojis found!")

        embed = Embed(title="Duplicates Found!")
        emojis_marked: List[Emoji] = []

        for index, (key, emojis) in enumerate(duplicates.items()):
            emojis_marked.extend(emojis[1:])

            embed.add_field(
                name=f"#{index + 1} {key}",
                value="\n".join([f"{emoji}: `{emoji.name}`" for emoji in emojis]),
            )

        await ctx.response.delete()
        await ctx.send(embed=embed)
        await ctx.prompt(
            f"Would you like to delete {plural(emojis_marked, md='`'):duplicate emoji}?"
        )

        async with ctx.typing():
            for emoji in emojis_marked:
                await emoji.delete(reason=f"Duplicate / {ctx.author.name}")

        log.info(
            f"Deleted {len(emojis_marked)} duplicate emojis from {ctx.guild} ({ctx.guild.id})."
        )
        return await ctx.approve(
            f"Deleted {plural(emojis_marked, md='`'):duplicate emoji}."
        )

    @emoji.group(
        name="archive",
        aliases=["zip"],
        invoke_without_command=True,
    )
    @has_permissions(manage_emojis=True)
    @cooldown(1, 30, BucketType.guild)
    async def emoji_archive(
        self: "Moderation",
        ctx: Context,
    ) -> Message:
        """
        Archive all emojis into a zip file.
        """

        if ctx.guild.premium_tier < 2:
            return await ctx.notice(
                "The server must have at least Level 2 to use this command!"
            )

        await ctx.neutral("Archiving emojis...")

        async with ctx.typing():
            buffer = BytesIO()
            with ZipFile(buffer, "w") as zip:
                for emoji in ctx.guild.emojis:
                    __buffer = await emoji.read()

                    zip.writestr(
                        f"{emoji.name}.{emoji.animated and 'gif' or 'png'}",
                        data=__buffer,
                    )

            buffer.seek(0)

        await ctx.response.delete()
        return await ctx.send(
            file=File(
                buffer,
                filename=f"{ctx.guild.name}_emojis.zip",
            ),
        )

    @emoji_archive.command(
        name="restore",
        aliases=["load"],
    )
    @has_permissions(manage_emojis=True)
    async def emoji_archive_restore(
        self: "Moderation",
        ctx: Context,
        attachment: Attachment,
    ) -> Message:
        """
        Restore emojis from an archive.
        """

        if not attachment.filename.endswith(".zip"):
            return await ctx.notice("You must provide a zip file to restore!")

        await ctx.neutral("Indexing archive...")

        emojis: List[Emoji] = []
        buffer = BytesIO()
        await attachment.save(buffer)

        with ZipFile(buffer, "r") as zip:
            if len(zip.namelist()) > (ctx.guild.emoji_limit - len(ctx.guild.emojis)):
                return await ctx.notice(
                    "The server does not have enough space for all the emojis in the archive!",
                    patch=ctx.response,
                )

            await ctx.neutral(
                f"Restoring {plural(len(zip.namelist()), md='`'):emoji}...",
                patch=ctx.response,
            )

            for name in zip.namelist():
                if not name.endswith((".png", ".gif")):
                    continue

                emoji = await ctx.guild.create_custom_emoji(
                    name=name[:-4],
                    image=zip.read(name),
                    reason=f"Archive / {ctx.author.name}",
                )
                emojis.append(emoji)

        await ctx.response.delete()
        return await ctx.approve(
            f"Restored {plural(emojis, md='`'):emoji} from [`{attachment.filename}`]({attachment.url})."
        )

    @executor_function
    def structure_sound(self: "Moderation", buffer: bytes) -> BytesIO:
        """
        Removes silence from an Audio Segment.
        """

        segment: AudioSegment = AudioSegment.from_file(BytesIO(buffer))
        if segment.duration_seconds > 5.2:
            chunks = silence(
                segment, min_silence_len=100, silence_thresh=-45, keep_silence=50
            )
            segment = AudioSegment.empty()

            for chunk in chunks:
                segment += chunk

        output = BytesIO()
        segment[: 5.2 * 1e3].export(output, format="ogg")

        return output

    @group(
        name="soundboard",
        aliases=["sound"],
    )
    @has_permissions(manage_guild=True)
    async def soundboard(self: "Moderation", ctx: Context) -> Message:
        """
        Various soundboard related commands.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @soundboard.command(
        name="add",
        aliases=["create"],
    )
    @has_permissions(manage_guild=True)
    async def soundboard_add(
        self: "Moderation",
        ctx: Context,
        attachment: Attachment,
        volume: Optional[Range[int, 1, 100]] = 100,
        *,
        name: Optional[str],
    ) -> None:
        """
        Add a sound to the soundboard.
        """

        name = name or attachment.description or xxh64_hexdigest(attachment.filename)
        if not guess_type(attachment.url)[0].startswith("audio/"):
            return await ctx.notice("You must provide an `mp3`, `wav`, or `ogg` file!")

        if len(name) < 2:
            return await ctx.notice("The name must be at least 2 characters long.")

        buffer = await attachment.read()
        sound = await self.structure_sound(buffer)
        await self.bot.http.request(
            Route(
                "POST",
                "/guilds/{guild_id}/soundboard-sounds",
                guild_id=ctx.guild.id,
            ),
            json={
                "name": name[:32],
                "sound": f"data:audio/ogg;base64,"
                + b64encode(sound.getvalue()).decode(),
                "volume": str(volume / 100),
            },
        )

        return await ctx.add_check()

    @soundboard.command(
        name="rename",
        aliases=["name"],
    )
    @has_permissions(manage_guild=True)
    async def soundboard_rename(
        self: "Moderation",
        ctx: Context,
        sound: Sound,
        *,
        name: str,
    ) -> Message:
        """
        Rename a sound in the server.
        """

        await self.bot.http.request(
            Route(
                "PATCH",
                "/guilds/{guild_id}/soundboard-sounds/{sound_id}",
                guild_id=ctx.guild.id,
                sound_id=sound.id,
            ),
            json={
                "name": name,
            },
        )
        return await ctx.approve(f"Renamed the sound to `{sound.name}`.")

    @soundboard.command(
        name="delete",
        aliases=[
            "remove",
            "del",
        ],
    )
    @has_permissions(manage_guild=True)
    async def soundboard_delete(
        self: "Moderation",
        ctx: Context,
        *,
        sound: Sound,
    ) -> Message:
        """
        Delete a sound from the server.
        """

        await self.bot.http.request(
            Route(
                "DELETE",
                "/guilds/{guild_id}/soundboard-sounds/{sound_id}",
                guild_id=ctx.guild.id,
                sound_id=sound.id,
            ),
        )
        return await ctx.approve(f"Deleted the sound `{sound.name}`.")
