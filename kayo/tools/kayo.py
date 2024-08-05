from os import environ
from pathlib import Path
from traceback import format_exception
from typing import TYPE_CHECKING, Dict, List, Optional

from aiohttp.client_exceptions import (
    ClientConnectorError,
    ClientResponseError,
    ContentTypeError,
)
from cashews import cache
from discord import (
    AllowedMentions,
    AuditLogEntry,
    Guild,
    HTTPException,
    Intents,
    Message,
    Status,
)
from discord.ext.commands import (
    BadColourArgument,
    BadFlagArgument,
    BadInviteArgument,
    BadLiteralArgument,
    Bot,
    ChannelNotFound,
    CheckFailure,
    CommandError,
    CommandNotFound,
    CommandOnCooldown,
    DisabledCommand,
    Flag,
    MemberNotFound,
    MissingPermissions,
    MissingRequiredArgument,
    MissingRequiredAttachment,
    MissingRequiredFlag,
    NotOwner,
    RangeError,
    RoleNotFound,
    UserInputError,
    UserNotFound,
    when_mentioned_or,
)
from discord.message import Message
from redis.asyncio import Redis
from tornado.ioloop import IOLoop
from xxhash import xxh128_hexdigest

import config
from tools.managers import ClientSession, Context, HelpCommand, database, logging
from tools.utilities import Error, codeblock

if TYPE_CHECKING:
    pass

cache.setup(f"redis://{config.Redis.host}", hash_key=config.Redis.hash)

log = logging.getLogger(__name__)
environ["JISHAKU_HIDE"] = "True"
environ["JISHAKU_RETAIN"] = "True"
environ["JISHAKU_NO_UNDERSCORE"] = "True"
environ["JISHAKU_SHELL_NO_DM_TRACEBACK"] = "True"


class Kayo(Bot):
    def __init__(self: "Kayo", *args, **kwargs):
        super().__init__(
            command_prefix=self.get_prefix,
            allowed_mentions=AllowedMentions(
                replied_user=False,
                everyone=False,
                roles=False,
                users=True,
            ),
            help_command=HelpCommand(),
            intents=Intents.all(),
            case_insensitive=True,
            owner_ids=config.owner_ids,
            status=Status.dnd,
            *args,
            **kwargs,
        )
        self.check(lambda ctx: ctx.guild)
        self.redis = Redis(
            db=config.Redis.db,
            host=config.Redis.host,
            port=config.Redis.port,
        )
        self.cache = cache
        self.traceback: Dict[str, Dict] = {}
        self.blacklist: List[int] = []
        self.session: ClientSession
        self.db: database.Pool
        self.ioloop: IOLoop
        self.run(
            config.token,
            log_handler=None,
        )

    @property
    def command_count(self: "Kayo") -> int:
        return len(set(self.walk_commands()))

    async def get_prefix(self: "Kayo", message: Message) -> List[str]:
        prefix = (
            await self.db.fetchval(
                """
                SELECT prefix
                FROM settings
                WHERE guild_id = $1
                """,
                message.guild.id,
            )
            or config.prefix
        )

        return when_mentioned_or(prefix)(self, message)

    async def setup_hook(self: "Kayo") -> None:
        self.session = ClientSession()
        self.ioloop = IOLoop.current()
        self.cb = self.ioloop.add_callback
        self.db = await database.connect()

        for feature in Path("features").iterdir():
            if not feature.is_dir():
                continue

            elif not (feature / "__init__.py").is_file():
                continue

            await self.load_extension(".".join(feature.parts))

    async def on_ready(self: "Kayo") -> None:
        log.info(f"Logged in as {self.user.name} with {self.command_count} commands.")

        whitelist_rows = await self.db.fetch("SELECT guild_id FROM whitelist")
        whitelist = [guild["guild_id"] for guild in whitelist_rows]

        for guild in self.guilds:
            if guild.owner_id in config.owner_ids:
                continue

            elif guild.id not in whitelist:
                log.info(f"Leaving {guild.name} ({guild.id}).")
                self.ioloop.add_callback(guild.leave)

    async def on_guild_join(self: "Kayo", guild: Guild) -> None:
        whitelist = await self.db.fetchrow(
            """
            SELECT user_id, receipt_id
            FROM whitelist
            WHERE guild_id = $1
            """,
            guild.id,
        )
        if whitelist:
            user = self.get_user(whitelist["user_id"]) or whitelist["user_id"]
            log.info(f"Joined {guild} purchased by {user} ({whitelist['receipt_id']}).")
            return

        elif guild.owner_id in config.owner_ids:
            return

        log.info(f"Leaving {guild.name} ({guild.id}).")
        self.ioloop.add_callback(guild.leave)

    async def process_commands(self: "Kayo", message: Message) -> None:
        if not message.guild:
            return

        elif message.author.id in self.blacklist:
            return

        return await super().process_commands(message)

    async def on_message_edit(self: "Kayo", before: Message, after: Message) -> None:
        if before.content == after.content:
            return

        await self.on_message(after)

    async def get_context(self: "Kayo", message: Message, *, cls=Context) -> Context:
        return await super().get_context(message, cls=cls)

    async def on_command(self: "Kayo", ctx: Context) -> None:
        log.info(
            f"{ctx.author} ({ctx.author.id}) executed {ctx.command} in {ctx.guild} ({ctx.guild.id})."
        )

    async def on_command_error(
        self: "Kayo", ctx: Context, exception: CommandError
    ) -> Optional[Message]:
        exception = getattr(exception, "original", exception)
        if type(exception) in (
            NotOwner,
            CheckFailure,
            UserInputError,
            DisabledCommand,
            CommandNotFound,
        ):
            return

        if isinstance(exception, CommandOnCooldown):
            return self.ioloop.add_callback(
                ctx.message.add_reaction,
                "⏰",
            )

        elif isinstance(
            exception, (UserNotFound, MemberNotFound, ChannelNotFound, RoleNotFound)
        ):
            return await ctx.notice("The provided entity wasn't found!")

        elif isinstance(exception, MissingPermissions):
            return await ctx.notice(
                f"You don't have sufficient permissions to use `{ctx.command}`!"
            )

        elif isinstance(exception, RangeError):
            return await ctx.notice(
                f"The value must be between `{exception.minimum}` and `{exception.maximum}`, received `{exception.value}`!"
            )

        elif isinstance(exception, BadInviteArgument):
            return await ctx.notice("The provided invite wasn't found!")

        # elif isinstance(exception, BadArgument):
        #     return await ctx.notice(
        #         f"Failed to convert `{exception.param}` to an `{exception.name}`!"
        #     )

        elif isinstance(exception, BadFlagArgument):
            flag: Flag = exception.flag
            argument: str = exception.argument

            return await ctx.notice(
                f"Failed to convert `{flag}` with input `{argument}`"
                + (f"\n> {flag.description}" if flag.description else "")
            )

        elif isinstance(exception, BadColourArgument):
            color: str = exception.argument

            return await ctx.notice(
                f"Color `{color}` is not valid!"
                + (
                    "\n> Ensure it starts with `#`."
                    if not color.startswith("#") and len(color) == 6
                    else ""
                )
            )

        elif isinstance(exception, MissingRequiredAttachment):
            return await ctx.notice("You need to provide an attachment!")

        elif isinstance(
            exception,
            (MissingRequiredArgument, MissingRequiredFlag, BadLiteralArgument),
        ):
            return await ctx.send_help(ctx.command)

        elif isinstance(exception, Error):
            return await ctx.notice(exception.message)

        elif isinstance(exception, HTTPException):
            code: int = exception.code

            if code == 50045:
                return await ctx.notice("The provided asset is too large!")

            elif code == 50013:
                return await ctx.notice("I am missing sufficient permissions!")

            elif code == 60003 and self.application:
                return await ctx.notice(
                    f"`{self.application.owner}` doesn't have **2FA** enabled!"
                )

            elif code == 50035:
                return await ctx.notice(
                    f"I wasn't able to send the message!\n>>> {codeblock(exception.text)}"
                )

        elif isinstance(exception, ClientConnectorError):
            return await ctx.notice("The **API** timed out during the request!")

        elif isinstance(exception, ClientResponseError):
            return await ctx.notice(
                f"The third party **API** returned a `{exception.status}`"
                + (
                    f" [*`{exception.message}`*](https://http.cat/{exception.status})"
                    if exception.message
                    else "!"
                )
            )

        elif isinstance(exception, ContentTypeError):
            return await ctx.notice("The **API** returned malformed content!")

        error_code = xxh128_hexdigest(
            f"{ctx.channel.id}:{ctx.message.id}",
            seed=1337,
        )
        self.traceback[error_code] = {
            "traceback": format_exception(exception),
            "command": ctx.command,
            "user": ctx.author,
            "guild": ctx.guild,
            "channel": ctx.channel,
            "timestamp": ctx.message.created_at,
        }

        return await ctx.notice(
            f"An unhandled exception occurred while processing `{ctx.command}`!"
            f"\n> I've stored the traceback as [`{error_code}`]({config.support})."
        )

    async def on_audit_log_entry_create(self, entry: AuditLogEntry) -> None:
        if not self.is_ready():
            return

        event = f"audit_log_entry_{entry.action.name}"
        self.dispatch(event, entry)
