"""Tests for Pocket Casts playback status handling."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from music_assistant_models.enums import MediaType
from music_assistant_models.errors import MediaNotFoundError, RetriesExhausted
from music_assistant_models.media_items import PodcastEpisode

from music_assistant.providers.pocketcasts import PocketCastsProvider
from tests.common import use_real_create_task


@pytest.fixture
def client() -> AsyncMock:
    """Return a mocked Pocket Casts API client with no show notes on offer."""
    client = AsyncMock()
    client.get_show_notes.return_value = {}
    client.get_podcast.return_value = {"uuid": "podcast-1", "title": "Podcast One"}
    return client


@pytest.fixture
def provider(client: AsyncMock) -> PocketCastsProvider:
    """Return a PocketCastsProvider backed by the mocked API client and a cold cache."""
    mass = AsyncMock()
    # force a cache miss so the wrapped fetches always run
    mass.cache.get_with_freshness = AsyncMock(return_value=(None, False, False))
    use_real_create_task(mass)
    manifest = MagicMock()
    manifest.domain = "pocketcasts"
    config = MagicMock()
    config.instance_id = "pocketcasts"
    config.get_value.return_value = None
    prov = PocketCastsProvider(mass, manifest, config)
    prov._client = client
    # handle_async_init would set this up; it is skipped here since it also logs in
    prov._announced_episodes = set()
    return prov


def _feed_episode(**overrides: Any) -> dict[str, Any]:
    """Build a full-podcast feed episode (snake_case schema, no playback status)."""
    return {
        "uuid": "episode-1",
        "title": "Episode 1",
        "url": "https://example.com/ep1.mp3",
        "file_type": "audio/mpeg",
        "duration": 1800,
        **overrides,
    }


async def test_sync_survives_episode_without_duration(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """A feed episode with a null duration must not abort the episode listing."""
    client.get_podcast_episodes.return_value = (
        "Podcast One",
        [
            _feed_episode(uuid="episode-1", duration=None),
            _feed_episode(uuid="episode-2", duration=1800),
        ],
    )
    client.get_in_progress_episodes.return_value = []
    client.get_history.return_value = []

    episodes = [episode async for episode in provider.get_podcast_episodes("podcast-1")]

    assert [episode.item_id for episode in episodes] == [
        "podcast-1:episode-1",
        "podcast-1:episode-2",
    ]
    assert episodes[0].duration == 0
    assert episodes[0].fully_played is False
    assert episodes[0].resume_position_ms == 0


async def test_sync_survives_null_status_fields(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Null playedUpTo/duration on an in-progress entry must not abort the listing."""
    client.get_podcast_episodes.return_value = (
        "Podcast One",
        [_feed_episode(uuid="episode-1", duration=None)],
    )
    client.get_in_progress_episodes.return_value = [
        {"uuid": "episode-1", "playedUpTo": None, "duration": None}
    ]
    client.get_history.return_value = []

    episodes = [episode async for episode in provider.get_podcast_episodes("podcast-1")]

    assert len(episodes) == 1
    assert episodes[0].fully_played is False
    assert episodes[0].resume_position_ms == 0


async def test_sync_still_marks_played_episodes(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """An episode played past the threshold is still reported as fully played."""
    client.get_podcast_episodes.return_value = ("Podcast One", [_feed_episode(duration=1000)])
    client.get_in_progress_episodes.return_value = [
        {"uuid": "episode-1", "playedUpTo": 950, "duration": 1000}
    ]
    client.get_history.return_value = []

    episodes = [episode async for episode in provider.get_podcast_episodes("podcast-1")]

    assert episodes[0].fully_played is True
    assert episodes[0].resume_position_ms == 0


async def test_sync_reports_resume_position(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """A partially played episode keeps its resume position."""
    client.get_podcast_episodes.return_value = ("Podcast One", [_feed_episode(duration=1000)])
    client.get_in_progress_episodes.return_value = [
        {"uuid": "episode-1", "playedUpTo": 300, "duration": 1000}
    ]
    client.get_history.return_value = []

    episodes = [episode async for episode in provider.get_podcast_episodes("podcast-1")]

    assert episodes[0].fully_played is False
    assert episodes[0].resume_position_ms == 300000


async def test_get_podcast_episode_handles_null_fields(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """The single-episode endpoint must tolerate null duration/playedUpTo."""
    client.get_episode_details.return_value = {
        "uuid": "episode-1",
        "title": "Episode 1",
        "url": "https://example.com/ep1.mp3",
        "fileType": "audio/mpeg",
        "duration": None,
        "playedUpTo": None,
        "playingStatus": 1,
    }

    # call the undecorated function so the @use_cache wrapper stays out of the test
    get_podcast_episode = cast("Any", PocketCastsProvider.get_podcast_episode).__wrapped__
    episode = await get_podcast_episode(provider, "podcast-1:episode-1")

    assert episode.fully_played is False
    assert episode.resume_position_ms == 0


async def test_get_resume_position_handles_null_fields(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """A null playedUpTo/duration on an in-progress entry yields a zero resume point."""
    client.get_in_progress_episodes.return_value = [
        {"uuid": "episode-1", "playedUpTo": None, "duration": None}
    ]

    assert await provider.get_resume_position("podcast-1:episode-1", MediaType.PODCAST_EPISODE) == (
        False,
        0,
        None,
    )


async def test_episodes_get_their_own_description_and_artwork(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """An episode uses its own show notes and artwork when the podcast supplies them."""
    client.get_podcast_episodes.return_value = (
        "Podcast One",
        [
            _feed_episode(uuid="episode-1"),
            _feed_episode(uuid="episode-2"),
        ],
    )
    client.get_in_progress_episodes.return_value = []
    client.get_history.return_value = []
    client.get_show_notes.return_value = {
        "episode-1": {
            "description": "<p>All about episode one.</p>",
            "image": "https://example.com/ep1.jpg",
        }
    }

    episodes = [episode async for episode in provider.get_podcast_episodes("podcast-1")]

    assert episodes[0].metadata.description == "<p>All about episode one.</p>"
    assert episodes[0].metadata.images is not None
    assert episodes[0].metadata.images[0].path == "https://example.com/ep1.jpg"


async def test_episodes_without_artwork_keep_the_podcast_cover(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Only some episodes have their own artwork, the rest fall back to the podcast cover."""
    client.get_podcast_episodes.return_value = ("Podcast One", [_feed_episode(uuid="episode-2")])
    client.get_in_progress_episodes.return_value = []
    client.get_history.return_value = []
    client.get_show_notes.return_value = {"episode-2": {"description": "Just notes."}}

    episodes = [episode async for episode in provider.get_podcast_episodes("podcast-1")]

    assert episodes[0].metadata.description == "Just notes."
    assert episodes[0].metadata.images is not None
    assert episodes[0].metadata.images[0].path.endswith("/podcast-1.jpg")


async def test_sync_survives_unavailable_show_notes(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """A failing show notes lookup must not abort the episode listing."""
    client.get_podcast_episodes.return_value = ("Podcast One", [_feed_episode(uuid="episode-1")])
    client.get_in_progress_episodes.return_value = []
    client.get_history.return_value = []
    client.get_show_notes.side_effect = RetriesExhausted("gave up")

    episodes = [episode async for episode in provider.get_podcast_episodes("podcast-1")]

    assert [episode.item_id for episode in episodes] == ["podcast-1:episode-1"]
    assert episodes[0].metadata.description is None


async def test_single_episode_gets_its_description(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Fetching one episode also fills in its description."""
    client.get_episode_details.return_value = {
        "uuid": "episode-1",
        "title": "Episode 1",
        "url": "https://example.com/ep1.mp3",
        "duration": 1800,
    }
    client.get_show_notes.return_value = {"episode-1": {"description": "All about it."}}

    episode = await provider.get_podcast_episode("podcast-1:episode-1")

    assert episode.metadata.description == "All about it."


async def test_episodes_name_their_podcast(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """An episode carries its podcast's name, which players show next to the title."""
    client.get_podcast_episodes.return_value = ("Podcast One", [_feed_episode(uuid="episode-1")])
    client.get_in_progress_episodes.return_value = []
    client.get_history.return_value = []

    episodes = [episode async for episode in provider.get_podcast_episodes("podcast-1")]

    assert episodes[0].podcast.name == "Podcast One"


async def test_single_episode_names_its_podcast(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Fetching one episode also names its podcast."""
    client.get_episode_details.return_value = {
        "uuid": "episode-1",
        "title": "Episode 1",
        "url": "https://example.com/ep1.mp3",
        "duration": 1800,
    }

    episode = await provider.get_podcast_episode("podcast-1:episode-1")

    assert episode.podcast.name == "Podcast One"


async def test_special_folder_episodes_name_their_podcast(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Episodes in the mixed folders name the podcast they came from."""
    client.get_starred_episodes.return_value = [
        {
            "uuid": "episode-1",
            "title": "Episode 1",
            "url": "https://example.com/ep1.mp3",
            "podcast": {"uuid": "podcast-1", "title": "Podcast One"},
        }
    ]

    items = await provider.browse("pocketcasts://starred")

    episodes = [item for item in items if isinstance(item, PodcastEpisode)]
    assert [episode.podcast.name for episode in episodes] == ["Podcast One"]


async def test_special_folder_episodes_use_the_name_from_the_payload(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """A podcast title carried by the folder payload is used without a podcast lookup."""
    client.get_history.return_value = [
        {
            "uuid": "episode-1",
            "title": "Episode 1",
            "url": "https://example.com/ep1.mp3",
            "podcastUuid": "podcast-1",
            "podcastTitle": "Podcast One",
        }
    ]

    items = await provider.browse("pocketcasts://history")

    episodes = [item for item in items if isinstance(item, PodcastEpisode)]
    assert [episode.podcast.name for episode in episodes] == ["Podcast One"]
    client.get_podcast.assert_not_called()


async def test_special_folder_looks_each_podcast_up_once(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Episodes of the same podcast share a single name lookup for the whole folder."""
    client.get_history.return_value = [
        {
            "uuid": f"episode-{index}",
            "title": f"Episode {index}",
            "url": f"https://example.com/ep{index}.mp3",
            "podcast": "podcast-1",
        }
        for index in (1, 2, 3)
    ]

    items = await provider.browse("pocketcasts://history")

    episodes = [item for item in items if isinstance(item, PodcastEpisode)]
    assert [episode.podcast.name for episode in episodes] == ["Podcast One"] * 3
    assert client.get_podcast.await_count == 1


def _up_next_episode(index: int, **overrides: Any) -> dict[str, Any]:
    """Build an Up Next queue entry as the up_next/list endpoint returns it."""
    return {
        "uuid": f"episode-{index}",
        "title": f"Episode {index}",
        "url": f"https://example.com/ep{index}.mp3",
        "podcast": {"uuid": "podcast-1", "title": "Podcast One"},
        "duration": 1000,
        **overrides,
    }


async def _up_next_tracks(provider: PocketCastsProvider) -> list[PodcastEpisode]:
    """Return the Up Next playlist tracks, which are always podcast episodes."""
    tracks = await provider.get_playlist_tracks("up_next")
    assert all(isinstance(track, PodcastEpisode) for track in tracks)
    return cast("list[PodcastEpisode]", tracks)


def _episode_item(provider: PocketCastsProvider) -> PodcastEpisode:
    """Build a PodcastEpisode the way the provider itself builds one, mappings included."""
    episode = provider._convert_episode(
        _up_next_episode(1), "podcast-1", podcast_name="Podcast One"
    )
    assert episode is not None
    return episode


async def test_up_next_is_offered_as_a_playlist(provider: PocketCastsProvider) -> None:
    """The Up Next queue shows up as a single editable playlist of podcast episodes."""
    playlists = [playlist async for playlist in provider.get_library_playlists()]

    assert len(playlists) == 1
    assert playlists[0].item_id == "up_next"
    assert playlists[0].is_editable is True
    assert playlists[0].supported_mediatypes == {MediaType.PODCAST_EPISODE}
    assert await provider.get_playlist("up_next") == playlists[0]


async def test_unknown_playlist_is_not_found(provider: PocketCastsProvider) -> None:
    """Pocket Casts has only the one queue, so any other playlist id is unknown."""
    with pytest.raises(MediaNotFoundError):
        await provider.get_playlist("some-other-list")
    with pytest.raises(MediaNotFoundError):
        await provider.get_playlist_tracks("some-other-list")


async def test_up_next_tracks_keep_the_queue_order(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """The queue order is the playlist order, and is reported through the item positions."""
    client.get_up_next_episodes.return_value = [_up_next_episode(index) for index in (3, 1, 2)]

    tracks = await _up_next_tracks(provider)

    assert [track.item_id for track in tracks] == [
        "podcast-1:episode-3",
        "podcast-1:episode-1",
        "podcast-1:episode-2",
    ]
    assert [track.position for track in tracks] == [0, 1, 2]


async def test_up_next_tracks_carry_their_resume_point(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """A queued episode already part-listened resumes where it was left off."""
    client.get_up_next_episodes.return_value = [
        _up_next_episode(1, playedUpTo=300, playingStatus=2),
        _up_next_episode(2),
    ]

    tracks = await _up_next_tracks(provider)

    assert tracks[0].resume_position_ms == 300000
    assert tracks[0].fully_played is False
    assert tracks[1].resume_position_ms == 0


async def test_up_next_has_no_second_page(provider: PocketCastsProvider, client: AsyncMock) -> None:
    """The whole queue arrives in one response, so paging past it stops the listing."""
    client.get_up_next_episodes.return_value = [_up_next_episode(1)]

    assert await provider.get_playlist_tracks("up_next", page=1) == []


async def test_adding_episodes_appends_them_in_order(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Added episodes are appended to the end of the queue, in the requested order."""
    client.get_episode_details.side_effect = lambda uuid: {
        "uuid": uuid,
        "title": f"Title {uuid}",
        "url": f"https://example.com/{uuid}.mp3",
        "published": "2026-01-01T00:00:00Z",
    }

    await provider.add_playlist_tracks("up_next", ["podcast-1:episode-1", "podcast-2:episode-2"])

    assert [call.kwargs["episode_uuid"] for call in client.play_last.await_args_list] == [
        "episode-1",
        "episode-2",
    ]
    first_call = client.play_last.await_args_list[0].kwargs
    assert first_call["podcast_uuid"] == "podcast-1"
    assert first_call["title"] == "Title episode-1"
    assert first_call["url"] == "https://example.com/episode-1.mp3"
    client.play_now.assert_not_called()


async def test_removing_resolves_positions_against_the_live_queue(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Positions are resolved against a fresh listing, since the queue moves on its own."""
    client.get_up_next_episodes.return_value = [_up_next_episode(index) for index in (1, 2, 3)]

    await provider.remove_playlist_tracks("up_next", (0, 2))

    client.remove_from_up_next.assert_awaited_once_with("episode-1", "episode-3")


async def test_removing_nothing_leaves_the_queue_alone(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Positions that no longer exist must not remove some other episode in their place."""
    client.get_up_next_episodes.return_value = [_up_next_episode(1)]

    await provider.remove_playlist_tracks("up_next", (5,))

    client.remove_from_up_next.assert_awaited_once_with()


async def test_queue_entry_without_a_podcast_is_skipped(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """An entry naming no podcast cannot be turned into an item, and must not sink the rest."""
    client.get_up_next_episodes.return_value = [
        {"uuid": "episode-0", "title": "Orphan", "url": "https://example.com/ep0.mp3"},
        _up_next_episode(1),
    ]

    tracks = await _up_next_tracks(provider)

    assert [track.item_id for track in tracks] == ["podcast-1:episode-1"]
    assert tracks[0].position == 0


async def test_browse_folders_report_playback_status(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """The mixed folders carry playback status inline, so their episodes should show it."""
    client.get_history.return_value = [
        _up_next_episode(1, playedUpTo=600, playingStatus=2),
        _up_next_episode(2, playingStatus=3),
    ]

    items = await provider.browse("pocketcasts://history")

    episodes = [item for item in items if isinstance(item, PodcastEpisode)]
    assert [episode.resume_position_ms for episode in episodes] == [600000, 0]
    assert [episode.fully_played for episode in episodes] == [False, True]


async def test_finishing_an_episode_clears_it_from_the_queue(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """A finished episode leaves Up Next, so the queue does not keep serving it back."""
    episode = _episode_item(provider)

    await provider.on_played(
        MediaType.PODCAST_EPISODE, "podcast-1:episode-1", True, 990, episode, is_playing=False
    )

    client.mark_episode_played.assert_awaited_once_with("podcast-1", "episode-1")
    client.remove_from_up_next.assert_awaited_once_with("episode-1")


async def test_stopping_early_leaves_the_episode_queued(
    provider: PocketCastsProvider, client: AsyncMock
) -> None:
    """Stopping partway is not finishing, so the episode keeps its place in Up Next."""
    episode = _episode_item(provider)

    await provider.on_played(
        MediaType.PODCAST_EPISODE, "podcast-1:episode-1", True, 200, episode, is_playing=False
    )

    client.remove_from_up_next.assert_not_awaited()
    client.mark_episode_played.assert_not_awaited()
    client.update_episode_progress.assert_awaited_once_with("podcast-1", "episode-1", 200)
