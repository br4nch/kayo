from typing import Any, List, Sequence


def remove(value: str, *args: str) -> str:
    for arg in args:
        value = value.replace(arg, "")

    return value


def codeblock(value: str, language: str = "") -> str:
    return f"```{language}\n{value}```"


def sanitize(value: str) -> str:
    return remove(value, "`", "*", "_", "~", "|", ">", "<" "/", "\\")


def shorten(value: str, length: int = 24) -> str:
    if len(value) > length:
        value = value[: length - 2] + (".." if len(value) > length else "").strip()

    return value


def human_join(seq: Sequence[str], delim: str = ", ", final: str = "or") -> str:
    size = len(seq)
    if size == 0:
        return ""

    if size == 1:
        return seq[0]

    if size == 2:
        return f"{seq[0]} {final} {seq[1]}"

    return f"{delim.join(seq[:-1])} {final} {seq[-1]}"


class plural:
    def __init__(
        self: "plural",
        value: int | str | List[Any],
        number: bool = True,
        md: str = "",
    ):
        self.value: int = (
            len(value)
            if isinstance(value, list)
            else (
                int(value.split(" ", 1)[-1])
                if value.startswith(("CREATE", "DELETE"))
                else int(value)
            )
            if isinstance(value, str)
            else value
        )
        self.number: bool = number
        self.md: str = md

    def __format__(self: "plural", format_spec: str) -> str:
        v = self.value
        singular, sep, plural = format_spec.partition("|")
        plural = plural or f"{singular}s"
        result = f"{self.md}{v:,}{self.md} " if self.number else ""

        result += plural if abs(v) != 1 else singular
        return result
