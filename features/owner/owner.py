from datetime import datetime
from traceback import format_exception
from typing import Optional

from discord import Embed, Guild, Member, Message, TextChannel, User
from discord.ext.commands import (
    Cog,
    Command,
    ExtensionAlreadyLoaded,
    ExtensionFailed,
    ExtensionNotFound,
    ExtensionNotLoaded,
    command,
    group,
    param,
)
from discord.utils import format_dt

from tools.kayo import Kayo
from tools.managers import Context, FlagConverter


class WhitelistFlags(FlagConverter):
    user_id: int
    receipt_id: str


class Owner(Cog):
    def __init__(self, bot: Kayo):
        self.bot: Kayo = bot

    async def cog_check(self: "Owner", ctx: Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    async def cog_load(self):
        self.bot.blacklist = [
            row["user_id"]
            for row in await self.bot.db.fetch(
                """
                SELECT user_id FROM blacklist
                """,
            )
        ]

    @command(name="c")
    async def c(self: "Owner", ctx: Context) -> None:
        """
        Cleans up our messages :3
        """

        await ctx.message.delete()
        await ctx.channel.purge(
            limit=2e4, check=lambda m: m.author in (self.bot.user, ctx.author)
        )

    @group(
        name="server",
        aliases=["whitelist"],
    )
    async def server(self: "Owner", ctx: Context) -> Message:
        """
        Manage server payments.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @server.command(name="add")
    async def server_add(
        self: "Owner",
        ctx: Context,
        guild_id: int,
        *,
        flags: WhitelistFlags,
    ) -> None:
        """
        Allow kayo in a server.
        """

        if not flags.user_id:
            return await ctx.notice("You must provide the `user_id` flag!")

        await self.bot.db.execute(
            """
            INSERT INTO whitelist (guild_id, user_id, receipt_id)
            VALUES ($1, $2, $3)
            """,
            guild_id,
            flags.user_id,
            flags.receipt_id,
        )

        return await ctx.add_check()

    @server.command(name="remove")
    async def server_remove(self: "Owner", ctx: Context, guild: Guild) -> None:
        """
        Remove kayo from a server.
        """

        self.bot.ioloop.add_callback(guild.leave)
        await self.bot.db.execute(
            """
            DELETE FROM whitelist
            WHERE guild_id = $1
            """,
            guild.leave,
        )

        return await ctx.add_check()

    @server.command(name="list")
    async def server_list(
        self: "Owner",
        ctx: Context,
        *,
        user: Member | User,
    ) -> Message:
        """
        View all payments from a user.
        """

        whitelists = await self.bot.db.fetch(
            """
            SELECT guild_id, receipt_id
            FROM whitelist
            WHERE user_id = $1
            """,
            user.id,
        )
        if not whitelists:
            return await ctx.notice(f"`{user}` doesn't have any payments!")

        return await ctx.paginate(
            [
                f"**{self.bot.get_guild(whitelist['guild_id']) or whitelist['guild_id']}** ([`{whitelist['receipt_id']}`](https://cash.app/payments/{whitelist['receipt_id']}/receipt))"
                for whitelist in whitelists
            ],
            embed=Embed(
                title=f"Payments from {user}",
            ),
        )

    @group(
        name="blacklist",
        aliases=["bl"],
        invoke_without_command=True,
    )
    async def blacklist(
        self: "Owner",
        ctx: Context,
        user: Member | User,
        *,
        reason: str = param(
            converter=str,
            default="No reason provided",
            description="The reason for the blacklist.",
        ),
    ) -> None:
        """
        Prevent a user from using the bot.
        """

        if user.id in self.bot.blacklist:
            await self.bot.db.execute(
                """
                DELETE FROM blacklist
                WHERE user_id = $1
                """,
                user.id,
            )
            self.bot.blacklist.remove(user.id)
        else:
            await self.bot.db.execute(
                """
                INSERT INTO blacklist (user_id, reason)
                VALUES ($1, $2)
                """,
                user.id,
                reason,
            )
            self.bot.blacklist.append(user.id)

        await ctx.add_check()

    @blacklist.command(
        name="view",
        aliases=["check"],
    )
    async def blacklist_view(
        self: "Owner",
        ctx: Context,
        *,
        user: Member | User,
    ) -> Message:
        """
        View the reason for a user's blacklist.
        """

        if not (
            reason := await self.bot.db.fetchval(
                """
                SELECT reason
                FROM blacklist
                WHERE user_id = $1
                """,
                user.id,
            )
        ):
            return await ctx.notice(f"`{user}` is not blacklisted!")

        return await ctx.neutral(f"`{user}` was blacklisted for **{reason}**.")

    @command(
        name="traceback",
        aliases=["trace", "tb"],
    )
    async def traceback(
        self: "Owner", ctx: Context, error_code: Optional[str]
    ) -> Message:
        """
        View traceback for an error code.
        """

        if not error_code:
            error_code = list(self.bot.traceback.keys())[-1]

        if not (error := self.bot.traceback.get(error_code)):
            return await ctx.notice("The provided error code does not exist!")

        traceback: str = "".join(error["traceback"])
        command: Command = error["command"]
        user: User = error["user"]
        guild: Guild = error["guild"]
        channel: TextChannel = error["channel"]
        timestamp: datetime = error["timestamp"]

        embed = Embed(
            title=f"Traceback for {command}",
            description="```py\n" + traceback + "```",
        )
        embed.add_field(
            name="Information",
            value=(
                f"{format_dt(timestamp)}\n"
                f">>> User: **{user}** (`{user.id}`)\n"
                f"Guild: **{guild}** (`{guild.id}`)\n"
                f"Channel: **{channel}** (`{channel.id}`)\n"
            ),
        )

        return await ctx.send(embed=embed)

    @command(
        name="reload",
        aliases=["rl"],
    )
    async def reload(self: "Owner", ctx: Context, feature: str) -> Message:
        """
        Reload an existing feature.
        """

        try:
            await self.bot.reload_extension(feature)
        except (ExtensionNotFound, ExtensionFailed) as exception:
            traceback = "\n".join(format_exception(exception))

            return await ctx.notice(
                f"> Failed to reload `{feature}`!" f"\n```py\n{traceback}```"
            )
        except ExtensionNotLoaded:
            return await self.load(ctx, feature=feature)

        return await ctx.approve(f"Successfully reloaded `{feature}`.")

    @command(name="load")
    async def load(self: "Owner", ctx: Context, feature: str) -> Message:
        """
        Load an existing feature.
        """

        try:
            await self.bot.load_extension(feature)
        except ExtensionFailed as exception:
            traceback = "\n".join(format_exception(exception))

            return await ctx.notice(
                f"> Failed to load `{feature}`!" f"```py\n{traceback}```"
            )
        except ExtensionNotFound:
            return await ctx.notice(f"`{feature}` doesn't exist!")
        except ExtensionAlreadyLoaded:
            return await ctx.notice(f"`{feature}` is already loaded!")

        return await ctx.approve(f"Successfully loaded `{feature}`.")

    @command(name="unload")
    async def unload(self: "Owner", ctx: Context, feature: str) -> Message:
        """
        Unload an existing feature.
        """

        try:
            await self.bot.unload_extension(feature)
        except (ExtensionNotFound, ExtensionNotLoaded):
            return await ctx.notice(f"`{feature}` is not loaded!")

        return await ctx.approve(f"Successfully unloaded `{feature}`.")
