from typing import List, Literal, Optional

from bs4 import BeautifulSoup
from pydantic import BaseModel

from tools.managers import ClientSession
from tools.utilities import sanitize

# from jishaku.functools import executor_function


class OnlyFansFile(BaseModel):
    url: str
    mime: Literal["IMAGE", "VIDEO"]


class OnlyFansPost(BaseModel):
    id: int
    user: str
    caption: str
    thumbnail: Optional[str]

    @property
    def url(self: "OnlyFansPost") -> str:
        return f"https://onlyfans.com/{self.user}/post/{self.id}"

    async def files(self: "OnlyFansPost", session: ClientSession) -> List[OnlyFansFile]:
        files: List[OnlyFansFile] = []
        soup: BeautifulSoup = await session.request(
            f"https://coomer.party/onlyfans/user/{self.user}/post/{self.id}"
        )

        for file in soup.findAll("div", class_="post__thumbnail"):
            if not (image := file.find("img")):
                continue

            files.append(
                OnlyFansFile(
                    url=f"https:{image.attrs['src']}",
                    mime="IMAGE",
                )
            )

        for file in soup.findAll("video", class_="post__video"):
            if not (video := file.find("source")):
                continue

            files.append(
                OnlyFansFile(
                    url=video.attrs["src"],
                    mime="VIDEO",
                )
            )

        return list(reversed(files))


class OnlyFansUser(BaseModel):
    name: str
    posts: List[OnlyFansPost] = []

    @property
    def url(self: "OnlyFansUser") -> str:
        return f"https://onlyfans.com/{self.name}"

    @property
    def avatar_url(self: "OnlyFansUser") -> str:
        return f"https://img.coomer.party/icons/onlyfans/{self.name}"

    @property
    def banner_url(self: "OnlyFansUser") -> str:
        return f"https://img.coomer.party/banners/onlyfans/{self.name}"


def extract(soup: BeautifulSoup) -> List[OnlyFansPost]:
    posts: List[OnlyFansPost] = []

    if not (prop := soup.find("span", itemprop="name")):
        return []

    posts.extend(
        OnlyFansPost(
            id=card.attrs["data-id"],
            user=prop.text,
            caption=card.find("header").text.strip(),
            thumbnail=(
                f"https:{image.attrs['src']}" if (image := card.find("img")) else None
            ),
        )
        for card in soup.findAll("article", class_="post-card")
        # if card.find("footer").find("div").text.strip() != "No attachments"
    )
    return posts


async def next_page(
    session: ClientSession, soup: BeautifulSoup
) -> Optional[BeautifulSoup]:
    if not (menu := soup.find("menu")):
        return

    if page := menu.find("a", class_="next"):  # type: ignore
        return await session.request(f"https://coomer.party{page.attrs['href']}")  # type: ignore


async def lookup(session: ClientSession, name: str) -> Optional[OnlyFansUser]:
    soup: BeautifulSoup = await session.request(
        f"https://coomer.party/onlyfans/user/{sanitize(name)}"
    )

    prop = soup.find("span", itemprop="name")
    user = OnlyFansUser(
        name=(prop.text if prop else "Unknown"),
        posts=extract(soup),
    )
    if user.name == "Unknown":
        return

    page: Optional[BeautifulSoup] = soup
    while True:
        page = await next_page(session, page)
        if not page:
            break

        user.posts.extend(extract(page))

    return user
