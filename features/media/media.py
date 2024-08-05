from asyncio import TimeoutError, sleep
from contextlib import suppress
from io import BytesIO
from json import dumps
from re import search
from sys import getsizeof
from typing import Any, Dict, List, Optional, TypedDict

from aiohttp.client_exceptions import ClientOSError, ClientResponseError
from asyncpg import UniqueViolationError
from discord import Embed, File, Forbidden, HTTPException, Message, TextChannel
from discord.ext.commands import (
    BucketType,
    Cog,
    CooldownMapping,
    UserInputError,
    check,
    command,
    group,
    has_permissions,
    param,
)
from discord.ext.tasks import loop
from discord.utils import get
from humanize import intword, naturalsize
from jishaku.functools import executor_function
from munch import DefaultMunch, Munch
from typing_extensions import Set
from xxhash import xxh32_hexdigest, xxh64_hexdigest
from yarl import URL
from yt_dlp import DownloadError, YoutubeDL

from config import Authorization
from tools import services
from tools.kayo import Kayo
from tools.managers import Context, Username, logging
from tools.services import InstagramPost, InstagramProfile, InstagramStoryItem
from tools.utilities import plural, shorten

log = logging.getLogger(__name__)


class InstagramRecord(TypedDict):
    username: str
    user_id: int
    channel_ids: List[int]
    posts: List[str]


class Media(Cog):
    def __init__(self, bot: Kayo):
        self.bot: Kayo = bot
        self.services: Dict[str, Any] = {
            "TikTok": (
                r"\<?((?:https?://(?:vt|vm|www)\.tiktok\.com/(?:t/)?[a-zA-Z\d]+\/?|https?://(?:www\.)?tiktok\.com/[@\w.]+/video/\d+))(?:\/\?.*\>?)?\>?"
            ),
            "YouTube": (
                r"(youtu.*be.*)\/(watch\?v=|embed\/|v|shorts|)(.*?((?=[&#?])|$))"
            ),
            # "Instagram": (
            #     r"(?:http\:|https\:)?\/\/(?:www\.)?instagram\.com\/(?:p|tv|reel)\/(?P<shortcode>[a-zA-Z0-9_-]+)\/*"
            # ),
            "Facebook": (
                r"(?:http\:|https\:)?\/\/(?:www\.)?facebook\.com\/(?P<username>[a-zA-Z0-9_-]+)\/videos\/(?P<slug>[0-9]+)\/?",
                r"(?:http\:|https\:)?\/\/(?:www\.)?fb\.watch\/(?P<slug>[a-zA-Z0-9_-]+)\/?",
            ),
            "SoundCloud": (
                r"(?:http\:|https\:)?\/\/(?:www\.)?soundcloud\.com\/(?P<username>[a-zA-Z0-9_-]+)\/(?P<slug>[a-zA-Z0-9_-]+)",
                r"(?:http\:|https\:)?\/\/(?:www\.)?soundcloud\.app\.goo\.gl\/(?P<slug>[a-zA-Z0-9_-]+)",
                r"(?:http\:|https\:)?\/\/on.soundcloud\.com\/(?P<slug>[a-zA-Z0-9_-]+)",
            ),
            "SoundGasm": (
                r"https?://(?:www\.)?soundgasm\.net/u/(?P<username>[0-9a-zA-Z_-]+)/(?P<slug>[0-9a-zA-Z_-]+)"
            ),
            "BandCamp": (
                r"https?://(?P<username>[^/]+)\.bandcamp\.com/track/(?P<slug>[^/?#&]+)"
            ),
        }
        self._cooldown_mapping = CooldownMapping.from_cooldown(
            rate=1,
            per=5,
            type=BucketType.user,
        )
        self.ytdl = YoutubeDL(
            {
                "quiet": True,
                "logger": log,
                "format": "best",
                "verbose": False,
            }
        )
        self.headers: Dict[str, str] = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.5",
            "Alt-Used": "www.instagram.com",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Cookie": (
                f"sessionid={Authorization.Instagram.session_id}; "
                f"csrftoken={Authorization.Instagram.csrf_token};"
            ),
            "DNT": "1",
            "Host": "www.instagram.com",
            "Referer": "https://www.instagram.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "TE": "trailers",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:104.0) 20100101 Firefox/103.0",
            "X-ASBD-ID": "198387",
            "X-CSRFToken": Authorization.Instagram.csrf_token,
            "X-IG-App-ID": "936619743392459",
            "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest",
        }
        self.instagram_feeds.start()

    async def cog_unload(self) -> None:
        self.instagram_feeds.cancel()

    async def check_instagram(self: "Media", record: InstagramRecord) -> None:
        """
        Refresh Instagram posts for a user.
        """

        try:
            profile: InstagramProfile = await services.instagram.profile(
                self.bot.session,
                username=record["username"],
                with_posts=True,
            )
        except ClientResponseError as exc:
            if exc.status == 404:
                log.info(f"Removing task for Instagram user {record['username']!r}.")

                await self.bot.db.execute(
                    """
                    DELETE FROM feeds.instagram
                    WHERE username = $1
                    """,
                    record["username"],
                )

            elif exc.status == 401:
                log.warn(f"Received a 401 while requesting {record['username']!r}!")

                self.instagram_feeds.cancel()

            return

        posts: List[InstagramStoryItem | InstagramPost] = [
            post for post in profile.posts if post.short_code not in record["posts"]
        ]
        posts.extend(
            [
                story_item
                for story_item in await services.instagram.fetch_stories(
                    self.bot.session,
                    redistribute=False,
                    user_id=record["user_id"],
                )
                if story_item.short_code not in record["posts"]
            ]
        )
        if not posts:
            return

        log.info(
            f"Sending {plural(posts):new post} for {record['username']!r} to {plural(record['channel_ids']):channel}."
        )
        await self.bot.db.execute(
            """
            UPDATE feeds.instagram
            SET posts = ARRAY_CAT(posts, $2)
            WHERE user_id = $1
            """,
            record["user_id"],
            [post.short_code for post in posts],
        )

        schedule_deletion: Set[int] = ()
        for channel_id in record["channel_ids"]:
            channel = self.bot.get_channel(channel_id)
            client = channel.guild.me

            if not channel or not (
                client.guild_permissions.send_messages
                and client.guild_permissions.embed_links
                and client.guild_permissions.attach_files
            ):
                schedule_deletion.add(channel_id)

            for post in posts:
                embed = Embed(
                    color=0x2B2D31,
                    timestamp=post.created_at,
                )
                if isinstance(post, InstagramPost):
                    embed.url = post.url
                    embed.title = shorten(post.caption, 52)

                embed.set_author(
                    name=profile.username,
                    icon_url=profile.avatar.url,
                )
                embed.set_footer(
                    text=(
                        isinstance(post, InstagramPost)
                        and "Instagram"
                        or "Instagram Story"
                    ),
                    icon_url="https://www.instagram.com/static/images/ico/favicon-192.png/68d99ba29cc8.png",
                )

                try:
                    self.bot.cb(
                        channel.send,
                        embed=embed,
                        file=File(
                            BytesIO(post.asset.buffer),
                            filename=f"{post.asset.name}.{post.asset.extension}",
                        ),
                    )
                except Forbidden:
                    schedule_deletion.add(channel_id)

        if schedule_deletion:
            await self.bot.db.execute(
                """
                DELETE FROM feeds.instagram
                WHERE channel_id = ANY($1::BIGINT[])
                """,
                list(schedule_deletion),
            )

    @loop(minutes=15)
    async def instagram_feeds(self: "Media"):
        """
        Check for new posts & stories for Instagram users.
        """

        records: List[InstagramRecord] = await self.bot.db.fetch(
            """
            SELECT
                username,
                user_id,
                array_agg(channel_id) AS channel_ids,
                MAX(posts) as posts
            FROM feeds.instagram
            GROUP BY username, user_id
            """
        )
        for record in records:
            self.bot.cb(self.check_instagram, record)

    @instagram_feeds.before_loop
    async def instagram_feeds_before(self: "Media"):
        """
        Waits for the bot to cache all channels.
        """

        await self.bot.wait_until_ready()

    @executor_function
    def extract_data(self: "Media", url: URL | str, **params) -> Optional[Munch]:
        """
        Asynchronously run YouTubeDL.
        """

        data: Optional[Dict]
        try:
            data = self.ytdl.extract_info(
                url=str(url),
                download=False,
                **params,
            )
        except DownloadError:
            return

        if data:
            return DefaultMunch.fromDict(data)  # type: ignore

    @Cog.listener("on_message")
    async def check_service(self: "Media", message: Message):
        """
        Automatically repost social media services.
        """

        if (
            message.author.bot
            or not message.guild
            or not message.content
            or not message.content.lower().startswith(
                (
                    "kayo",
                    "slut",
                    message.guild.me.display_name,
                )
            )
        ):
            return

        ctx = await self.bot.get_context(message)

        for service, pattern in self.services.items():
            if isinstance(pattern, tuple):
                patterns = pattern
                pattern = patterns[0]
                short_patterns = patterns[1:]

                for short_pattern in short_patterns:
                    if not (match := search(short_pattern, message.content)):
                        continue

                    request = await self.bot.session.get(match.group())
                    message.content = str(request.url)
                    break

            if not (match := search(pattern, message.content)):
                continue

            if (
                bucket := self._cooldown_mapping.get_bucket(message)
            ) and bucket.update_rate_limit():
                break

            await ctx.typing()
            arguments = list(
                group.values() if (group := match.groupdict()) else [URL(match.group())]
            )
            self.bot.dispatch(
                f"{service.lower()}_request",
                ctx,
                *arguments,
            )

            log.info(
                f"{service} request from {ctx.author} for "
                + (
                    "/".join(arguments)
                    if isinstance(arguments[0], str)
                    else str(arguments[0])
                )
            )

            await sleep(1)
            if message.embeds and not message.mentions[1:]:
                with suppress(HTTPException):
                    await ctx.message.delete()

            break

    @Cog.listener()
    async def on_tiktok_request(self: "Media", ctx: Context, url: URL) -> Message:
        if not url.path.startswith("/@"):
            request = await self.bot.session.get(url)
            url = request.url

        aweme_id = url.parts[3]
        post = await services.tiktok.post(
            self.bot.session,
            aweme_id=aweme_id,
        )
        if not post:
            return await ctx.notice("That TikTok video could not be found!")

        embed = Embed(
            description=(
                f"[TikTok]({post.url}) requested by {ctx.author.mention}\n\n"
                + (shorten(post.caption, 30) if post.caption else "")
            ),
        )
        embed.set_author(
            url=post.user.url,
            name=post.user.nickname,
            icon_url=post.user.avatar_url,
        )
        embed.set_footer(
            text=f"💜 {intword(post.statistics.likes)} ✨ {intword(post.statistics.views)}",
            icon_url=post.music.cover_url,
        )

        if post.images:
            embeds: List[Embed] = []

            for image in post.images:
                page = embed.copy()
                page.set_image(url=image)

                embeds.append(page)

            return await ctx.paginate(embeds)

        buffer: bytes = await self.bot.session.request(post.video_url)
        if getsizeof(buffer) > ctx.guild.filesize_limit:
            return await ctx.notice(
                f"The video exceeds the maximum file size limit! (`{naturalsize(ctx.guild.filesize_limit)}`/`{naturalsize(getsizeof(buffer))}`)"
            )

        return await ctx.send(
            embed=embed,
            file=File(
                BytesIO(buffer),
                filename=f"TikTok{xxh64_hexdigest(aweme_id)}.mp4",
            ),
        )

    @Cog.listener()
    async def on_youtube_request(self: "Media", ctx: Context, url: URL) -> Message:
        data: Optional[Munch] = await self.extract_data(url)
        if not data:
            return await ctx.notice("That YouTube video could not be found!")

        buffer: bytes = await self.bot.session.request(data.url)
        if getsizeof(buffer) > ctx.guild.filesize_limit:
            return await ctx.notice(
                f"The video exceeds the maximum file size limit! (`{naturalsize(ctx.guild.filesize_limit)}`/`{naturalsize(getsizeof(buffer))}`)"
            )

        embed = Embed(
            description=(
                f"[YouTube Video]({data.webpage_url}) requested by {ctx.author.mention}\n\n"
                + data.title
            ),
        )
        embed.set_author(
            name=data.uploader,
            icon_url=ctx.author.display_avatar,
        )
        embed.set_footer(
            text=f"💜 {intword(data.like_count)} ✨ {intword(data.view_count)}",
        )

        return await ctx.send(
            embed=embed,
            file=File(
                BytesIO(buffer),
                filename=f"{data.title}.{data.ext}",
            ),
        )

    @Cog.listener()
    async def on_facebook_request(
        self: "Media", ctx: Context, username: str, slug: str
    ) -> Message:
        data: Optional[Munch] = await self.extract_data(
            f"https://facebook.com/{username}/videos/{slug}"
        )
        if not data:
            return await ctx.notice("That Facebook video could not be found!")

        buffer: bytes = await self.bot.session.request(data.url)
        if getsizeof(buffer) > ctx.guild.filesize_limit:
            return await ctx.notice(
                f"The video exceeds the maximum file size limit! (`{naturalsize(ctx.guild.filesize_limit)}`/`{naturalsize(getsizeof(buffer))}`)"
            )

        embed = Embed(
            description=(
                f"[Facebook Video]({data.webpage_url}) requested by {ctx.author.mention}\n\n"
                + data.description
                or ""
            ),
        )
        embed.set_author(
            name=data.uploader,
            icon_url=ctx.author.display_avatar,
        )
        embed.set_footer(
            text=f"✨ {intword(data.view_count)} ⏰ {data.duration_string}",
        )

        return await ctx.send(
            embed=embed,
            file=File(
                BytesIO(buffer),
                filename=f"{data.title}.{data.ext}",
            ),
        )

    @Cog.listener()
    async def on_soundcloud_request(
        self: "Media", ctx: Context, username: str, slug: str
    ) -> Message:
        data: Optional[Munch] = await self.extract_data(
            f"https://soundcloud.com/{username}/{slug}"
        )
        if not data:
            return await ctx.notice("That SoundCloud track could not be found!")

        buffer: bytes = await self.bot.session.request(data.formats[-1].url)
        if getsizeof(buffer) > ctx.guild.filesize_limit:
            return await ctx.notice(
                f"The audio exceeds the maximum file size limit! (`{naturalsize(ctx.guild.filesize_limit)}`/`{naturalsize(getsizeof(buffer))}`)"
            )

        return await ctx.send(
            file=File(
                BytesIO(buffer),
                filename=f"{data.title}.{data.ext}",
            ),
        )

    @Cog.listener()
    async def on_soundgasm_request(
        self: "Media", ctx: Context, username: str, slug: str
    ) -> Message:
        data: Optional[Munch] = await self.extract_data(
            f"https://soundgasm.net/u/{username}/{slug}"
        )
        if not data:
            return await ctx.notice("That SoundGasm track could not be found!")

        buffer: bytes = await self.bot.session.request(data.url)
        if getsizeof(buffer) > ctx.guild.filesize_limit:
            return await ctx.notice(
                f"The audio exceeds the maximum file size limit! (`{naturalsize(getsizeof(buffer))}`/`{naturalsize(ctx.guild.filesize_limit)}`)"
            )

        return await ctx.send(
            file=File(
                BytesIO(buffer),
                filename=f"{data.title}.{data.ext}",
            ),
        )

    @Cog.listener()
    async def on_bandcamp_request(
        self: "Media", ctx: Context, username: str, slug: str
    ) -> Message:
        data: Optional[Munch] = await self.extract_data(
            f"https://{username}.bandcamp.com/slug/{id}"
        )
        if not data:
            return await ctx.notice("That BandCamp track could not be found!")

        buffer: bytes = await self.bot.session.request(data.url)
        if getsizeof(buffer) > ctx.guild.filesize_limit:
            return await ctx.notice(
                f"The audio exceeds the maximum file size limit! (`{naturalsize(ctx.guild.filesize_limit)}`/`{naturalsize(getsizeof(buffer))}`)"
            )

        return await ctx.send(
            file=File(
                BytesIO(buffer),
                filename=f"{data['title']}.{data.ext}",
            ),
        )

    @command(
        name="pinterest",
        aliases=["pin"],
    )
    async def pinterest(
        self: "Media",
        ctx: Context,
        username: str = param(
            converter=Username,
            description="The username to lookup.",
        ),
    ) -> Message:
        """
        Lookup information for a Pinterest profile.
        """

        await ctx.typing()
        data: Munch = await self.bot.session.request(
            "https://www.pinterest.com/resource/UserResource/get/",
            params={
                "source_url": f"/{username}/",
                "data": dumps(
                    {
                        "options": {
                            "field_set_key": "unauth_profile",
                            "is_mobile_fork": True,
                            "username": username,
                        },
                        "context": {},
                    }
                ),
            },
            slug="resource_response.data",
        )

        embed = Embed(
            url=f"https://pinterest.com/{data.username}/",
            title=f"{data.full_name} (@{data.username})",
            description=data.about or data.website_url,
        )
        embed.set_thumbnail(url=data.image_xlarge_url)

        embed.add_field(
            name="Pins",
            value=f"{data.pin_count:,}",
        )
        embed.add_field(
            name="Followers",
            value=f"{data.follower_count:,}",
        )
        embed.add_field(
            name="Following",
            value=f"{data.following_count:,}",
        )

        return await ctx.send(embed=embed)

    async def display_stories(
        self: "Media",
        ctx: Context,
        user: InstagramProfile,
    ) -> None:
        if user.is_private:
            return

        story_items: List[InstagramStoryItem] = await services.instagram.fetch_stories(
            self.bot.session,
            user_id=user.id,
        )
        if not story_items:
            return

        try:
            await ctx.prompt(
                f"**{user.full_name}** has an active story, would you like to view it?"
            )
        except UserInputError:
            return

        message = await ctx.paginate([story.asset.url for story in story_items])
        await message.delete(delay=250)

    @group(
        name="instagram",
        aliases=["insta", "ig"],
        invoke_without_command=True,
    )
    async def instagram(
        self: "Media",
        ctx: Context,
        username: str = param(
            converter=Username,
            description="The username to lookup.",
        ),
    ) -> Message:
        """
        Lookup information for an Instagram profile.
        """

        async with ctx.typing():
            user: InstagramProfile = await services.instagram.profile(
                self.bot.session,
                username=username.lower(),
            )

        embed = Embed(
            url=user.url,
            title=(
                f"{user.full_name} (@{user.username})"
                if user.username != user.full_name
                else user.username
            ),
            description=user.biography,
        )
        embed.set_thumbnail(url=user.avatar.url)

        embed.add_field(
            name="Posts",
            value=f"{user.post_count:,}",
        )
        embed.add_field(
            name="Followers",
            value=f"{user.followers:,}",
        )
        embed.add_field(
            name="Following",
            value=f"{user.following:,}",
        )

        if not user.is_private:
            self.bot.cb(self.display_stories, ctx, user)

        return await ctx.send(embed=embed)

    @instagram.command(
        name="add",
        aliases=["feed"],
    )
    @has_permissions(manage_guild=True)
    async def instagram_add(
        self: "Media",
        ctx: Context,
        channel: TextChannel,
        username: str = param(
            converter=Username,
            description="The username to lookup.",
        ),
    ) -> Message:
        """
        Send notifications for an Instagram user.
        """

        async with ctx.typing():
            user: InstagramProfile = await services.instagram.profile(
                self.bot.session,
                username=username.lower(),
            )
            if user.is_private:
                return await ctx.notice(
                    f"Instagram profile [`@{user.username}`]({user.url}) is private!"
                )

        try:
            await self.bot.db.execute(
                """
                INSERT INTO feeds.instagram (
                    username,
                    user_id,
                    guild_id,
                    channel_id
                ) VALUES ($1, $2, $3, $4)
                """,
                user.username,
                user.id,
                ctx.guild.id,
                channel.id,
            )
        except UniqueViolationError:
            return await ctx.notice(
                f"An Instagram feed already exists for [`@{user.username}`]({user.url})!"
            )

        return await ctx.approve(
            f"Now sending notifications for [`@{user.username}`]({user.url}) in {channel.mention}."
        )

    @instagram.command(
        name="remove",
        aliases=[
            "delete",
            "del",
            "rm",
        ],
    )
    @has_permissions(manage_guild=True)
    async def instagram_remove(
        self: "Media",
        ctx: Context,
        username: str = param(
            converter=Username,
            description="The username to stream.",
        ),
    ) -> Message:
        """
        Remove an existing Instagram feed.
        """

        result = await self.bot.db.execute(
            """
            DELETE FROM feeds.instagram
            WHERE guild_id = $1
            AND LOWER(username) = $2
            """,
            ctx.guild.id,
            username.lower(),
        )
        if result == "DELETE 0":
            return await ctx.notice(
                f"An Instagram feed doesn't exist for `@{username}`!"
            )

        return await ctx.approve(
            f"No longer sending notifications for [`@{username}`](https://instagram.com/{username})."
        )

    @instagram.command(
        name="clean",
        aliases=["clear"],
    )
    @has_permissions(manage_guild=True)
    async def instagram_clean(
        self: "Media",
        ctx: Context,
    ) -> Message:
        """
        Remove all Instagram feeds.
        """

        result = await self.bot.db.execute(
            """
            DELETE FROM feeds.instagram
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if result == "DELETE 0":
            return await ctx.notice("No Instagram feeds exist!")

        return await ctx.approve(
            f"Successfully removed {plural(result, md='`'):Instagram feed}."
        )

    @instagram.command(name="list")
    @has_permissions(manage_guild=True)
    async def instagram_list(
        self: "Media",
        ctx: Context,
    ) -> Message:
        """
        View all Instagram feeds.
        """

        records = await self.bot.db.fetch(
            """
            SELECT username, channel_id
            FROM feeds.instagram
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if not records:
            return await ctx.notice("No Instagram feeds exist!")

        return await ctx.paginate(
            [
                f"{channel.mention} - [`@{record['username']}`](https://instagram.com/{record['username']})"
                for record in records
                if (channel := ctx.guild.get_channel(record["channel_id"]))
            ],
            embed=Embed(title="Instagram Feeds"),
        )

    @command(
        name="onlyfans",
        aliases=["of"],
    )
    @check(
        lambda ctx: ctx.guild
        and ctx.guild.id == 1128849931269062688
        and ctx.author.premium_since
    )
    async def onlyfans(self: "Media", ctx: Context, *, name: str) -> Message:
        """
        Repost content from an OnlyFans profile.
        """

        async with ctx.typing():
            user = await services.onlyfans.lookup(
                self.bot.session,
                name=name,
            )
            if not user:
                return await ctx.notice(f"Couldn't locate an OnlyFans for `{name}`.")

        if get(ctx.guild.text_channels, name=user.name):
            return await ctx.notice(
                f"Channel already exists for [`{user.name}`]({user.url})."
            )

        await ctx.prompt(
            f"Found {plural(user.posts, md='**'):post} by [`{user.name}`]({user.url}), would you like to continue?"
        )

        channel = await ctx.guild.create_text_channel(
            name=user.name,
            nsfw=True,
            reason=f"Upload task by {ctx.author}",
            category=get(ctx.guild.categories, name="onlyfans"),
        )
        await ctx.neutral(
            f"Beginning the upload task for [`{user.name}`]({user.url}) in {channel.mention}..."
        )

        for post in user.posts:
            text = post.caption
            prepared: List[File] = []

            for file in await post.files(self.bot.session):
                try:
                    buffer = await self.bot.session.request(file.url)
                except (ClientOSError, TimeoutError):
                    continue

                prepared.append(
                    File(
                        BytesIO(buffer),
                        filename=f"{user.name}-{post.id}-{xxh32_hexdigest(file.url)}.{'png' if file.mime == 'IMAGE' else 'mp4'}",
                    )
                )

                if len(prepared) == 5:
                    try:
                        await channel.send(
                            content=text,
                            files=prepared,
                        )
                    except HTTPException:
                        pass

                    text = None
                    prepared.clear()

            if prepared:
                try:
                    await channel.send(
                        content=text,
                        files=prepared,
                    )
                except HTTPException:
                    pass

        return await ctx.approve(
            f"Finished uploading {plural(user.posts, md='**'):post} by [`{user.name}`]({user.url}) in {channel.mention}."
        )
