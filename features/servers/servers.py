from typing import Optional

from asyncpg import UniqueViolationError
from discord import Embed, Member, Message, TextChannel
from discord.ext.commands import Cog, command, group, has_permissions

import config
from tools.kayo import Kayo
from tools.managers import Context, GuildProxy, MemberProxy, Script, logging
from tools.utilities import plural

log = logging.getLogger(__name__)


class Servers(Cog):
    def __init__(self, bot: Kayo):
        self.bot: Kayo = bot

    @Cog.listener("on_member_join")
    async def members_increase(self: "Servers", member: Member):
        """
        Increase the daily join metrics.
        """

        key = f"m:j:{member.guild.id}"
        await self.bot.redis.incr(key)

    @Cog.listener("on_member_remove")
    async def members_decrease(self: "Servers", member: Member):
        """
        Decrease the daily join metrics.
        """

        key = f"m:j:{member.guild.id}"
        await self.bot.redis.decr(key)

    @Cog.listener("on_member_join")
    async def welcome_send(self: "Servers", member: Member):
        """
        Send the greet messages for a member.
        """

        for record in await self.bot.db.fetch(
            """
                SELECT channel_id, template
                FROM welcome_messages
                WHERE guild_id = $1
                """,
            member.guild.id,
        ):
            channel: TextChannel = self.bot.get_channel(record["channel_id"])
            if channel:
                script = Script(
                    record["template"],
                    variables={
                        "user": MemberProxy(member),
                        "guild": GuildProxy(member.guild),
                    },
                )

                await script.send(channel)

    @command(name="prefix")
    @has_permissions(manage_guild=True)
    async def prefix(
        self: "Servers",
        ctx: Context,
        prefix: Optional[str],
    ) -> Message:
        """
        View or change the server prefix.
        """

        if not prefix:
            prefix = (
                await self.bot.db.fetchval(
                    """
                    SELECT prefix
                    FROM settings
                    WHERE guild_id = $1
                    """,
                    ctx.guild.id,
                )
                or config.prefix
            )

            return await ctx.neutral(f"Server prefix: `{prefix}`")

        await self.bot.db.execute(
            """
            INSERT INTO settings (guild_id, prefix)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE
            SET prefix = EXCLUDED.prefix
            """,
            ctx.guild.id,
            prefix.lower(),
        )
        return await ctx.approve(f"Set the server prefix to `{prefix}`")

    @group(
        name="welcome",
        aliases=["welc", "greet"],
    )
    @has_permissions(manage_guild=True)
    async def welcome(self: "Servers", ctx: Context) -> Message:
        """
        Automatically greet new members.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @welcome.command(
        name="add",
        aliases=["create"],
    )
    @has_permissions(manage_guild=True)
    async def welcome_add(
        self: "Servers",
        ctx: Context,
        channel: TextChannel,
        *,
        script: Script,
    ) -> Message:
        """
        Add a new welcome message.
        """

        try:
            await self.bot.db.execute(
                "INSERT INTO welcome_messages VALUES($1, $2, $3)",
                ctx.guild.id,
                channel.id,
                script.template,
            )
        except UniqueViolationError:
            return await ctx.notice(
                f"A welcome message already exists in {channel.mention}!"
            )

        return await ctx.approve(f"Now greeting new members in {channel.mention}.")

    @welcome.command(
        name="remove",
        aliases=[
            "delete",
            "del",
            "rm",
        ],
    )
    @has_permissions(manage_guild=True)
    async def welcome_remove(
        self: "Servers",
        ctx: Context,
        *,
        channel: TextChannel,
    ) -> Message:
        """
        Remove an existing welcome message.
        """

        result = await self.bot.db.execute(
            """
            DELETE FROM welcome_messages
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            ctx.guild.id,
            channel.id,
        )
        if result == "DELETE 0":
            return await ctx.notice(
                f"A welcome message doesn't exist in {channel.mention}!"
            )

        return await ctx.approve(
            f"No longer greeting new members in {channel.mention}."
        )

    @welcome.command(
        name="check",
        aliases=[
            "view",
            "emit",
        ],
    )
    @has_permissions(manage_guild=True)
    async def welcome_check(
        self: "Servers",
        ctx: Context,
        *,
        channel: TextChannel,
    ) -> Message:
        """
        View an existing welcome message.
        """

        template = await self.bot.db.fetchval(
            """
            SELECT template
            FROM welcome_messages
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            ctx.guild.id,
            channel.id,
        )
        if not template:
            return await ctx.notice(
                f"A welcome message doesn't exist in {channel.mention}!"
            )

        script = Script(
            template,
            variables={
                "user": MemberProxy(ctx.author),
                "guild": GuildProxy(ctx.guild),
            },
        )
        return await script.send(ctx)

    @welcome.command(
        name="clean",
        aliases=["clear"],
    )
    @has_permissions(manage_guild=True)
    async def welcome_clean(
        self: "Servers",
        ctx: Context,
    ) -> Message:
        """
        Remove all welcome messages.
        """

        result = await self.bot.db.execute(
            """
            DELETE FROM welcome_messages
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if result == "DELETE 0":
            return await ctx.notice("No welcome messages exist!")

        return await ctx.approve(
            f"Successfully removed {plural(result, md='`'):welcome message}."
        )

    @welcome.command(name="list")
    @has_permissions(manage_guild=True)
    async def welcome_list(
        self: "Servers",
        ctx: Context,
    ) -> Message:
        """
        View all welcome messages.
        """

        records = await self.bot.db.fetch(
            """
            SELECT channel_id
            FROM welcome_messages
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if not records:
            return await ctx.notice("No welcome messages exist!")

        return await ctx.paginate(
            [
                channel.mention
                for record in records
                if (channel := ctx.guild.get_channel(record["channel_id"]))
            ],
            embed=Embed(title="Welcome Messages"),
        )
