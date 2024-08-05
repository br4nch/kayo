from re import Match, compile, sub
from typing import Any, Callable, Dict, Optional, Union

from discord import Embed, Guild, User, Member, Message
from discord.abc import GuildChannel
from typing_extensions import Type

from tools.managers import Context


class Script:
    def __init__(
        self,
        template: str,
        variables: Dict[str, Union["GuildProxy", "MemberProxy"]] = {},
    ):
        self.variables = variables
        self.template = sub(r"{([a-zA-Z_.]+)}", self.parse_variable, template)
        self.pattern = compile(r"{(.*?)}")
        self.data: Dict[str, Union[Dict, str]] = {
            "embed": {},
        }
        self.compile()

    @property
    def components(self) -> Dict[str, Callable[[Any], None]]:
        return {
            "content": lambda value: self.data.update({"content": value}),
            "url": lambda value: self.data["embed"].update({"url": value}),
            "color": lambda value: self.data["embed"].update({"color": int(value, 16)}),
            "title": lambda value: self.data["embed"].update({"title": value}),
            "description": (
                lambda value: self.data["embed"].update({"description": value})
            ),
            "thumbnail": (
                lambda value: self.data["embed"].update({"thumbnail": {"url": value}})
            ),
            "image": (
                lambda value: self.data["embed"].update({"image": {"url": value}})
            ),
            "footer": (
                lambda value: self.data["embed"]
                .setdefault("footer", {})
                .update({"text": value})
            ),
            "footer.icon": (
                lambda value: self.data["embed"]
                .setdefault("footer", {})
                .update({"icon_url": value})
            ),
            "author": (
                lambda value: self.data["embed"]
                .setdefault("author", {})
                .update({"name": value})
            ),
            "author.icon": (
                lambda value: self.data["embed"]
                .setdefault("author", {})
                .update({"icon_url": value})
            ),
            "author.url": (
                lambda value: self.data["embed"]
                .setdefault("author", {})
                .update({"url": value})
            ),
        }

    def parse_variable(self, match: Match) -> str:
        name = match.group(1)
        value = self.variables

        try:
            for attr in name.split("."):
                value = value[attr]

            return str(value)
        except (AttributeError, TypeError, KeyError):
            return match.group(1)

    def compile(self) -> None:
        for match in self.pattern.findall(self.template):
            parts = match.split(":", 1)
            if len(parts) == 2:
                name, value = map(str.strip, parts)
                if not name in self.components:
                    continue

                self.components[name](value)

        if not any(self.data.get(key) for key in ["content", "embed"]):
            self.data["content"] = self.template

    async def send(self, target: Context | GuildChannel, **kwargs) -> Message:
        return await target.send(
            content=self.data.get("content"),
            embed=(
                Embed.from_dict(self.data["embed"]) if self.data.get("embed") else None
            ),
            **kwargs,
        )

    @classmethod
    async def convert(cls: Type["Script"], ctx: Context, argument: str) -> "Script":
        return cls(
            template=argument,
            variables={
                "user": MemberProxy(ctx.author),
                "guild": GuildProxy(ctx.guild),
            },
        )

    def __repr__(self) -> str:
        return f"<Parser template={self.template!r}>"

    def __str__(self) -> str:
        return self.template


class BaseProxy:
    def __getitem__(self, name: str):
        if name.startswith("__"):
            raise AttributeError

        return getattr(self, name)

    def __str__(self) -> Optional[str]:
        return getattr(self, "name")


class GuildProxy(BaseProxy):
    def __init__(self, guild: Guild):
        self.id = guild.id
        self.name = guild.name
        self.icon = guild.icon
        self.members = guild.member_count
        self.boosts = guild.premium_subscription_count


class UserProxy(BaseProxy):
    def __init__(self, user: User):
        self.id = user.id
        self.bot = user.bot
        self.name = user.name
        self.mention = user.mention
        self.avatar = user.display_avatar.url
        self.display_name = user.display_name
        self.created_at = user.created_at.strftime("%m/%d/%Y, %I:%M %p")


class MemberProxy(UserProxy):
    def __init__(self, member: Member):
        super().__init__(member)

        self.guild = GuildProxy(member.guild)
        self.joined_at = member.joined_at.strftime("%m/%d/%Y, %I:%M %p")
        self.booster = bool(member.premium_since)
        self.boost_since = (
            member.premium_since
            and member.premium_since.strftime("%m/%d/%Y, %I:%M %p")
            or "Not a booster"
        )
        self.roles = ", ".join([role.name for role in member.roles[1:]])
