"""
Tests for the first-letter jump on library listings.

A large library is unreachable by scrolling: the listing pages 50 items at a time, so
getting to T means dozens of round-trips. ``starts_from`` drops everything filed before
a letter, leaving the listing starting there and running on to the end - a jump rather
than a filter, so scrolling carries on into the letters after it.

The interesting part is *which* name it compares against - it has to be the one the
current ordering uses, or the jump lands somewhere the list does not agree with.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from music_assistant_models.media_items import Artist, ProviderMapping

from music_assistant.mass import MusicAssistant

pytestmark = pytest.mark.asyncio

# name -> sort_name, covering every way a name can land somewhere surprising
SEEDED_ARTISTS: dict[str, str | None] = {
    "The Beatles": "Beatles, The",
    "Talk Talk": None,
    "Tool": None,
    "Ätna": None,
    "Sigur Rós": None,
    "1975": None,
    "!!!": None,
}


def _mapping() -> ProviderMapping:
    """Create a provider mapping with a unique provider item id."""
    return ProviderMapping(
        item_id=uuid4().hex,
        provider_domain="prov_a",
        provider_instance="prov_a_inst",
        in_library=True,
    )


@pytest.fixture(scope="module")
async def lettered_mass(music_mass_module: MusicAssistant) -> MusicAssistant:
    """Return a database-only instance seeded with the artists above."""
    for name, sort_name in SEEDED_ARTISTS.items():
        await music_mass_module.music.artists.add_item_to_library(
            Artist(
                item_id="0",
                provider="library",
                name=name,
                sort_name=sort_name,
                provider_mappings={_mapping()},
            )
        )
    return music_mass_module


async def _names(mass: MusicAssistant, **kwargs: Any) -> list[str]:
    """Return the names of the library artists matching the given listing arguments."""
    return [item.name for item in await mass.music.artists.library_items(**kwargs)]


async def test_the_letters_after_it_are_still_there(lettered_mass: MusicAssistant) -> None:
    """The point of a jump: what follows the letter stays in the listing, in order."""
    from_s = await _names(lettered_mass, starts_from="s", order_by="name")
    assert from_s == ["Sigur Rós", "Talk Talk", "The Beatles", "Tool"]


async def test_it_starts_at_the_letter(lettered_mass: MusicAssistant) -> None:
    """Everything filed before the letter is gone, so the listing opens where asked."""
    from_t = await _names(lettered_mass, starts_from="t", order_by="name")
    assert from_t[0] == "Talk Talk"
    assert "Sigur Rós" not in from_t
    assert "1975" not in from_t


async def test_it_follows_the_sort_name(lettered_mass: MusicAssistant) -> None:
    """Sorted by sort name, "The Beatles" is filed under B, so a jump to T is past it."""
    assert "The Beatles" in await _names(lettered_mass, starts_from="b", order_by="sort_name")
    assert "The Beatles" not in await _names(lettered_mass, starts_from="t", order_by="sort_name")


async def test_it_follows_the_plain_name(lettered_mass: MusicAssistant) -> None:
    """Sorted by name, the same artist is filed under T, so a jump to T reaches it."""
    assert "The Beatles" in await _names(lettered_mass, starts_from="t", order_by="name")


async def test_diacritics_are_folded(lettered_mass: MusicAssistant) -> None:
    """A name is filed under the letter it reads as, not the codepoint it starts with."""
    from_a = await _names(lettered_mass, starts_from="a", order_by="name")
    assert "Ätna" in from_a
    # and the unlettered names sort below 'a', so a jump to A is already past them
    assert "1975" not in from_a
    assert "!!!" not in from_a


async def test_case_is_ignored(lettered_mass: MusicAssistant) -> None:
    """The caller may send either case for the letter."""
    assert await _names(lettered_mass, starts_from="T", order_by="name") == await _names(
        lettered_mass, starts_from="t", order_by="name"
    )


async def test_a_letter_with_nothing_of_its_own_lands_on_the_next_one(
    lettered_mass: MusicAssistant,
) -> None:
    """Nothing is filed under Q, so jumping there opens at whatever follows it."""
    assert await _names(lettered_mass, starts_from="q", order_by="name") == await _names(
        lettered_mass, starts_from="s", order_by="name"
    )


async def test_composes_with_the_other_filters(lettered_mass: MusicAssistant) -> None:
    """A jump narrows the same result set the other filters do, rather than replacing it."""
    assert await _names(lettered_mass, starts_from="s", search="tool", order_by="name") == ["Tool"]
    assert not await _names(lettered_mass, starts_from="u", search="tool", order_by="name")


@pytest.mark.parametrize("value", ["ab", "1", "$", "#"])
@pytest.mark.parametrize("bound", ["starts_from", "starts_before"])
async def test_rejects_a_value_that_is_not_a_letter(
    lettered_mass: MusicAssistant, value: str, bound: str
) -> None:
    """Anything that is not a single letter is refused, not quietly ignored."""
    with pytest.raises(ValueError, match="letter"):
        await lettered_mass.music.artists.library_items(**{bound: value})


async def test_the_way_back_up_out_of_a_jump(lettered_mass: MusicAssistant) -> None:
    """
    starts_before with the ordering reversed is how a jumped listing walks back up.

    Asked that way it returns the items immediately before the letter, nearest first, so
    the caller reverses a page and puts it above what it already has.
    """
    before_t = await _names(lettered_mass, starts_before="t", order_by="name_desc")
    assert before_t == ["Sigur Rós", "Ätna", "1975", "!!!"]
    # reversed, that is exactly the run that precedes the jump
    assert list(reversed(before_t)) + await _names(
        lettered_mass, starts_from="t", order_by="name"
    ) == await _names(lettered_mass, order_by="name")


async def test_the_two_bounds_meet_without_overlapping(lettered_mass: MusicAssistant) -> None:
    """Whatever letter it is cut at, no item is in both halves and none is in neither."""
    for letter in "abcdefghijklmnopqrstuvwxyz":
        above = await _names(lettered_mass, starts_from=letter, order_by="sort_name")
        below = await _names(lettered_mass, starts_before=letter, order_by="sort_name")
        assert not set(above) & set(below), letter
        assert sorted(above + below) == sorted(SEEDED_ARTISTS), letter


async def test_an_empty_value_is_no_jump_at_all(lettered_mass: MusicAssistant) -> None:
    """Falsy means the whole library, the same way the other filters read it."""
    assert sorted(await _names(lettered_mass, starts_from="")) == sorted(SEEDED_ARTISTS)


async def test_the_jump_reads_the_index_rather_than_scanning(
    lettered_mass: MusicAssistant,
) -> None:
    """
    The point of a comparison on the name column is that SQLite can seek into the index.

    A LIKE or a substr() would be correct too but leaves the planner free to scan the whole
    table, which is the case this exists to avoid on a library with thousands of rows.
    """
    database = lettered_mass.music.database
    controller = lettered_mass.music.artists
    captured: dict[str, Any] = {}
    orig = database.get_rows_from_query

    async def spy(
        query: str,
        params: dict[str, Any] | None = None,
        limit: int = 500,
        offset: int = 0,
        _captured: dict[str, Any] = captured,
        _orig: Any = orig,
    ) -> list[Any]:
        _captured["query"], _captured["params"] = query, params
        result: list[Any] = await _orig(query, params, limit=limit, offset=offset)
        return result

    with patch.object(database, "get_rows_from_query", spy):
        await controller.library_items(starts_from="t", order_by="sort_name")
    plan_rows = await database.get_rows_from_query(
        f"EXPLAIN QUERY PLAN {captured['query']} LIMIT 500 OFFSET 0",
        captured["params"],
        limit=0,
    )
    details = [row["detail"] for row in plan_rows]
    table = controller.db_table
    assert any(
        f"{table}_search_sort_name_idx" in detail and "SEARCH" in detail for detail in details
    ), details
