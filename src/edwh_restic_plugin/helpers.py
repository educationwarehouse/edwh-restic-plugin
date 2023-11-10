import typing

T = typing.TypeVar("T")


def fix_tags(tags: typing.Iterable[T | None]) -> list[T]:
    """
    Removes all None type elements from the input list.

    :param tags: list of strings, some elements may be None
    :return: list of strings with all None elements removed
    """
    return [tag for tag in tags if tag is not None]


def camel_to_snake(s: str) -> str:
    return "".join([f"_{c.lower()}" if c.isupper() else c for c in s]).lstrip("_")
