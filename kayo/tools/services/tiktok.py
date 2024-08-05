from datetime import datetime
from typing import List, Optional

from munch import Munch
from pydantic import BaseModel

from tools.managers import ClientSession


class TikTokUserStatistics(BaseModel):
    following: int = 0
    followers: int = 0
    likes: int = 0


class TikTokPostStatistics(BaseModel):
    views: int = 0
    likes: int = 0
    shares: int = 0
    comments: int = 0
    downloads: int = 0


class TikTokMusic(BaseModel):
    id: int
    author: str
    title: str
    album: Optional[str]
    download_url: str
    cover_url: Optional[str]


class TikTokUser(BaseModel):
    id: int
    username: str
    nickname: str
    avatar_url: str
    signature: str
    statistics: Optional[TikTokUserStatistics] = TikTokUserStatistics()

    @property
    def url(self: "TikTokUser") -> str:
        return f"https://tiktok.com/@{self.username}"


class TikTokPost(BaseModel):
    id: int
    caption: Optional[str]
    created_at: datetime
    user: TikTokUser
    music: TikTokMusic
    statistics: TikTokPostStatistics
    video_url: Optional[str]
    images: List[str] = []

    @property
    def url(self: "TikTokPost") -> str:
        return f"https://tiktok.com/@{self.user.username}/video/{self.id}"


async def post(session: ClientSession, aweme_id: str) -> Optional[TikTokPost]:
    data: Munch = await session.request(
        "https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/feed/",
        params={
            "aweme_id": aweme_id,
        },
    )

    post = data.aweme_list[0]
    if post.aweme_id != aweme_id:
        return

    return TikTokPost(
        id=post.aweme_id,
        caption=post.desc,
        created_at=post.create_time,
        user=TikTokUser(
            id=post.author.uid,
            username=post.author.unique_id,
            nickname=post.author.nickname,
            avatar_url=post.author.avatar_larger.url_list[-1],
            signature=post.author.signature,
        ),
        music=TikTokMusic(
            id=post.music.id,
            author=post.music.author,
            title=post.music.title,
            album=post.music.album or None,
            download_url=post.music.play_url.url_list[-1],
            cover_url=(cover.url_list[-1] if (cover := post.music.cover_hd) else None),
        ),
        statistics=TikTokPostStatistics(
            views=post.statistics.play_count,
            likes=post.statistics.digg_count,
            shares=post.statistics.share_count,
            comments=post.statistics.comment_count,
            downloads=post.statistics.download_count,
        ),
        video_url=(video.play_addr.url_list[-1] if (video := post.video) else None),
        images=(
            [image.display_image.url_list[-1] for image in slides.images]
            if (slides := post.image_post_info)
            else []
        ),
    )
