from datetime import datetime
from typing import Dict, List, Optional

from aiohttp import ClientResponseError
from cashews import cache
from munch import Munch
from pydantic import BaseModel

from config import Authorization
from tools.managers import Asset, ClientSession, assets

cache.setup("mem://")


class InstagramMetrics(BaseModel):
    count: int


class InstagramPost(BaseModel):
    short_code: str
    caption: str
    asset: Asset
    created_at: datetime

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/p/{self.short_code}"


class InstagramProfile(BaseModel):
    id: int
    username: str
    full_name: str
    biography: Optional[str]
    avatar: Asset
    profile_pic_url_hd: str
    is_private: bool
    edge_owner_to_timeline_media: InstagramMetrics
    edge_followed_by: InstagramMetrics
    edge_follow: InstagramMetrics
    posts: List[InstagramPost] = []

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self.username}"

    # @property
    # def avatar(self) -> str:
    #     return self.profile_pic_url_hd

    @property
    def post_count(self) -> int:
        return self.edge_owner_to_timeline_media.count

    @property
    def followers(self) -> int:
        return self.edge_followed_by.count

    @property
    def following(self) -> int:
        return self.edge_follow.count


class InstagramStoryItem(BaseModel):
    short_code: str
    asset: Asset
    created_at: datetime


@cache(
    ttl="3m",
    key="{username}:{with_posts}",
)
async def profile(
    session: ClientSession,
    username: str,
    with_posts: bool = False,
) -> InstagramProfile:
    data: Munch = await session.request(
        f"https://www.instagram.com/{username}",
        params={
            "__a": "1",
            "__d": "dis",
        },
        headers=headers,
        slug="graphql.user",
    )

    data["avatar"] = await assets.save(
        session,
        buffer=data["profile_pic_url_hd"],
        prefix="Instagram",
    )

    posts: List[InstagramPost] = []
    if with_posts:
        for item in data.edge_owner_to_timeline_media.edges[:3]:
            post = item.node

            try:
                asset_url = (
                    post["is_video"] is True
                    and post["video_url"]
                    or post["display_url"]
                )

                asset: Asset = await assets.save(
                    session,
                    buffer=asset_url,
                    prefix="Instagram",
                    redistribute=False,
                )
            except ClientResponseError:
                break

            posts.append(
                InstagramPost(
                    asset=asset,
                    short_code=post["shortcode"],
                    caption=post["edge_media_to_caption"]["edges"][0]["node"]["text"],
                    created_at=post["taken_at_timestamp"],
                )
            )

    return InstagramProfile(**data, posts=posts)


@cache(
    ttl="3m",
    key="{user_id}:{redistribute}",
)
async def fetch_stories(
    session: ClientSession,
    user_id: int,
    redistribute: bool = True,
) -> List[InstagramStoryItem]:
    data: Munch = await session.request(
        "https://www.instagram.com/api/v1/feed/reels_media/",
        params={
            "reel_ids": user_id,
        },
        headers=headers,
        slug="reels_media",
    )

    story_items: List[InstagramStoryItem] = []
    if data:
        for item in data[0]["items"]:
            try:
                asset_url = (
                    item["media_type"] == 2
                    and item["video_versions"][0]["url"]
                    or item["image_versions2"]["candidates"][0]["url"]
                )

                asset: Asset = await assets.save(
                    session,
                    buffer=asset_url,
                    prefix="Instagram",
                    redistribute=redistribute,
                )
            except ClientResponseError:
                break

            story_items.append(
                InstagramStoryItem(
                    asset=asset,
                    short_code=item["code"],
                    created_at=item["taken_at"],
                )
            )

    return story_items


headers: Dict[str, str] = {
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
