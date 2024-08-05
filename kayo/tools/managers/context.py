from asyncio import TimeoutError as ATimeoutError
from contextlib import suppress
from math import ceil
from typing import TYPE_CHECKING, Any, Callable, List, Optional

from discord import Embed, Guild, Member, Message
from discord.embeds import EmbedProxy as EmbedField
from discord.ext.commands import Context as DefaultContext
from discord.ext.commands import FlagConverter
from discord.ext.commands import FlagConverter as DefaultFlagConverter
from discord.ext.commands import MinimalHelpCommand, UserInputError
from discord.ext.commands.core import Command
from discord.ext.commands.flags import FlagsMeta
from discord.utils import MISSING, as_chunks, cached_property

from features.music.player import Player
from tools.managers import database

from .paginator import Paginator

if TYPE_CHECKING:
    from tools.kayo import Kayo


class Context(DefaultContext):
    bot: "Kayo"
    guild: Guild
    author: Member
    player: Player
    lastfm: database.Record
    response: Optional[Message] = None

    @cached_property
    def replied_message(self: "Context") -> Optional[Message]:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, Message):
            return ref.resolved

    async def add_check(self: "Context"):
        await self.message.add_reaction("✅")

    async def send(self: "Context", *args, **kwargs) -> Message:
        embeds: List[Embed] = kwargs.get("embeds", [])
        if embed := kwargs.get("embed"):
            embeds.append(embed)

        for embed in embeds:
            self.style(embed)

        if patch := kwargs.pop("patch", None):
            kwargs.pop("reference", None)

            if args:
                kwargs["content"] = args[0]

            self.response = await patch.edit(**kwargs)
        else:
            self.response = await super().send(*args, **kwargs)

        return self.response

    async def neutral(self: "Context", value: str, *args, **kwargs) -> Message:
        patch: Optional[Message] = kwargs.pop("patch", None)
        reference: Optional[Message] = kwargs.pop("reference", None)

        embed = Embed(
            description=("> " if not ">" in value else "") + value,
            *args,
            **kwargs,
        )

        return await self.send(embed=embed, patch=patch, reference=reference)

    async def approve(self: "Context", value: str, *args, **kwargs) -> Message:
        patch: Optional[Message] = kwargs.pop("patch", None)
        reference: Optional[Message] = kwargs.pop("reference", None)

        embed = Embed(
            description=("> " if not ">" in value else "") + value,
            *args,
            **kwargs,
        )

        return await self.send(embed=embed, patch=patch, reference=reference)

    async def notice(self: "Context", value: str, *args, **kwargs) -> Message:
        patch: Optional[Message] = kwargs.pop("patch", None)
        reference: Optional[Message] = kwargs.pop("reference", None)

        embed = Embed(
            description=("> " if not ">" in value else "") + value,
            *args,
            **kwargs,
        )

        return await self.send(embed=embed, patch=patch, reference=reference)

    async def prompt(self: "Context", value: str, *args, **kwargs) -> None:
        embed = Embed(
            description=("> " if not ">" in value else "") + value,
            *args,
            **kwargs,
        )

        message = await self.send(embed=embed)
        for reaction in ("✅", "❌"):
            self.bot.cb(message.add_reaction, reaction)

        with suppress(ATimeoutError):
            reaction, _ = await self.bot.wait_for(
                "reaction_add",
                check=lambda reaction, user: (
                    user.id == self.author.id and reaction.message.id == message.id
                ),
                timeout=30,
            )

        await message.delete()
        if reaction.emoji == "✅":
            return True

        raise UserInputError("Prompt was declined.")

    async def paginate(
        self: "Context",
        data: List[Embed | EmbedField | str],
        embed: Optional[Embed] = None,
        max_results: int = 10,
        counter: bool = True,
    ) -> Message:
        compiled: List[Embed | str] = []

        if isinstance(data[0], Embed):
            for index, page in enumerate(data):
                if not isinstance(page, Embed):
                    continue

                self.style(page)
                if len(data) > 1:
                    if footer := page.footer:
                        page.set_footer(
                            text=f"{footer.text} ∙ Page {index + 1} of {len(data)}",
                            icon_url=footer.icon_url,
                        )
                    else:
                        page.set_footer(
                            text=f"Page {index + 1} of {len(data)}",
                        )

                compiled.append(page)

        elif isinstance(data[0], str) and embed:
            lines = 0
            pages = ceil(len(data) / max_results)
            self.style(embed)

            for chunk in as_chunks(data, max_results):
                page = embed.copy()
                page.description = f"{embed.description or ''}\n\n"

                for line in chunk:
                    lines += 1
                    page.description += (
                        f"`{lines}` {line}\n" if counter else f"{line}\n"
                    )

                if pages > 1:
                    if footer := page.footer:
                        page.set_footer(
                            text=f"{footer.text} ∙ Page {len(compiled) + 1} of {pages}",
                            icon_url=footer.icon_url,
                        )
                    else:
                        page.set_footer(
                            text=f"Page {len(compiled) + 1} of {pages}",
                        )

                compiled.append(page)

        elif isinstance(data[0], str) and not embed:
            for index, page in enumerate(data):
                compiled.append(f"{index + 1}/{len(data)} {page}")

        paginator = Paginator(self, compiled)
        return await paginator.begin()

    def style(self: "Context", embed: Embed) -> Embed:
        if not embed.color:
            embed.color = 0x2B2D31

        return embed


class HelpCommand(MinimalHelpCommand):
    context: Context

    def __init__(self: "HelpCommand", **options):
        super().__init__(
            command_attrs={
                "hidden": True,
                "aliases": ["h"],
            },
            **options,
        )

    def _add_flag_formatting(self, param: FlagConverter):
        optional: List[str] = [
            f"`--{name}`: {flag.description}"
            for name, flag in param.get_flags().items()
            if flag.default is not MISSING
        ]
        required: List[str] = [
            f"`--{name}`: {flag.description}"
            for name, flag in param.get_flags().items()
            if flag.default is MISSING
        ]

        if required:
            self.paginator.add_line("Required Flags:")
            for index, flag in enumerate(required):
                self.paginator.add_line(flag, empty=index == len(required) - 1)

        if optional:
            self.paginator.add_line("Optional Flags:")
            for flag in optional:
                self.paginator.add_line(flag)

    def add_command_formatting(
        self, command: Command[Any, Callable[..., Any], Any]
    ) -> None:
        super().add_command_formatting(command)

        for param in command.clean_params.values():
            if isinstance(param.annotation, FlagsMeta):
                self._add_flag_formatting(param.annotation)

    def command_not_found(self, string: str) -> str:
        return f"Command `{string}` does not exist!"

    async def send_error_message(self, error: str) -> Message:
        return await self.context.notice(error)

    async def send_pages(self) -> Message:
        pages = [Embed(description=page) for page in self.paginator.pages]
        return await self.context.paginate(pages)


class FlagConverter(
    DefaultFlagConverter, case_insensitive=True, prefix="--", delimiter=" "
):
    ...
