#!/usr/bin/env python3

import logging
import sys
import time
import traceback

from functools import partial
from multiprocessing import Pool
from typing import Any, List, Tuple, Callable, Optional, Set

import requests
import spotipy
import tidalapi
import tidalapi.playlist

from cachetools import cached, TTLCache
from tqdm import tqdm

from .type import *
from .tidalapi_patch import set_tidal_playlist
from .filters import *


logger = logging.getLogger(__name__)


@cached(TTLCache(maxsize=1024, ttl=600))
def _query_tidal(tidal_session: TidalSession, q: str, model):
    return tidal_session.search(q, models=[model])


@cached(TTLCache(maxsize=1024, ttl=600))
def query_tidal_album(tidal_session: TidalSession, artist: str, album: str):
    artist = simple(artist.casefold())
    album = simple(album.casefold())
    return _query_tidal(tidal_session, artist+" "+album, tidalapi.Album)


@cached(TTLCache(maxsize=1024, ttl=600))
def query_tidal_track(tidal_session: TidalSession, artist: str, track: str):
    artist = simple(artist.casefold())
    track = simple(track.casefold())
    return _query_tidal(tidal_session, artist+" "+track, tidalapi.Track)


def tidal_search(
    spotify_track_and_cache: Tuple[SpotifyTrack, TidalTrack | None],
    tidal_session: TidalSession,
) -> TidalTrack:
    spotify_track, cached_tidal_track = spotify_track_and_cache
    if cached_tidal_track:
        logger.debug("Found %s in cache.", spotify_track["name"])
        return cached_tidal_track
    # search for album name and first album artist
    if (
        "album" in spotify_track
        and "artists" in spotify_track["album"]
        and len(spotify_track["album"]["artists"])
    ):
        for artist in spotify_track["album"]["artists"]:
            artist_name = artist["name"]
            album_name = spotify_track["album"]["name"]
            album_result = query_tidal_album(tidal_session, artist_name, album_name)

            logger.debug(
                "Looking for album %s in Tidal" % spotify_track["album"]["name"]
            )
            for album in album_result["albums"]:
                album_tracks = album.tracks()
                if len(album_tracks) >= spotify_track["track_number"]:
                    track = album_tracks[spotify_track["track_number"] - 1]
                    if match(track, spotify_track):
                        return track

    # if that fails then search for track name and first artist
    logger.info(
        "Did not find track %s in any artist albums, running general search."
        % spotify_track["name"]
    )
    logger.debug("Searching spotify for %s", spotify_track["name"])
    spotify_track_name = spotify_track["name"]
    for artist in spotify_track["artists"]:
        query_tidal_track(tidal_session, artist["name"], spotify_track_name)
        artist_name = artist["name"]
        search_res  = query_tidal_track(tidal_session, artist=artist_name, track=spotify_track_name)
        res: TidalTrack | None = next(
            (x for x in search_res["tracks"] if match(x, spotify_track)), None
        )
        if res:
            logger.info("Found song %s in Tidal!", spotify_track["name"])
            return res
    logger.info("Could not find song %s" % spotify_track["name"])
    return res


def repeat_on_request_error(function: Callable, *args, **kwargs):
    # utility to repeat calling the function up to 5 times if an exception is thrown
    logging.root.addFilter(Filter429('tidalapi'))
    sleep_schedule = {
        5: 1,
        4: 10,
        3: 30,
        2: 60,
        1: 90,
    }  # sleep variable length of time depending on retry number
    for rem_attempts in range(5, 0, -1):
        try:
            return function(*args, **kwargs)
        except requests.exceptions.RequestException as e:
            if e.response.status_code != 429 or rem_attempts < 1:
                logger.critical("%s could not be recovered", e)
                logger.debug("Exception info: %s", traceback.format_exception(e))
                raise e
            logger.info(
                "429 occured on %s, retrying %d more times",
                e.request.path_url,
                rem_attempts,
            )
            if e.response is not None:
                logger.debug("Response message: %s", e.response.text)
                logger.debug("Response headers: %s", e.response.headers)
            time.sleep(sleep_schedule.get(rem_attempts, 1))

    logger.critical("Aborting sync")
    logger.critical("The following arguments were provided\n\n: %s", str(args))
    logger.exception(traceback.format_exc())
    sys.exit(1)


def _enumerate_wrapper(value_tuple: Tuple, function: Callable, **kwargs) -> List:
    # just a wrapper which accepts a tuple from enumerate and returns the index back as the first argument
    index, value = value_tuple
    logging.root.addFilter(Filter429('tidalapi'))
    return (index, repeat_on_request_error(function, value, **kwargs))


def call_async_with_progress(function, values, description, num_processes, **kwargs):
    results = len(values) * [None]
    cached = 0
    # Add the known results to the existing return var
    cached_results = filter(lambda x: x[1][1] is not None, enumerate(values))
    for idx, res in cached_results:
        results[idx] = res[1]
        cached += 1
    # Only search for non-cached songs
    to_search = filter(lambda x: x[1][1] is None, enumerate(values))
    with Pool(processes=num_processes) as process_pool:
        try:
            for index, result in tqdm(
                process_pool.imap_unordered(
                    partial(_enumerate_wrapper, function=function, **kwargs),
                    to_search,
                ),
                total=len(values) - cached,
                desc=description,
                unit="req",
            ):
                results[index] = result
        except KeyboardInterrupt:
            logger.critical("KeyboardInterrupt received. Killing pool.")
            process_pool.close()
            exit(1)
    return results


def get_tracks_from_spotify_playlist(
    spotify_session: SpotifySession, spotify_playlist
) -> List[SpotifyTrack]:
    output = []
    results: List[SpotifyTrack] = spotify_session.playlist_tracks(
        spotify_playlist["id"],
        fields="next,items(track(name,album(name,artists),artists,track_number,duration_ms,id,external_ids(isrc)))",
    )
    while True:
        output.extend([r["track"] for r in results["items"] if r["track"] is not None])
        # move to the next page of results if there are still tracks remaining in the playlist
        if results["next"]:
            results = spotify_session.next(results)
        else:
            return output


class TidalPlaylistCache:
    _existing: Any | None = None
    _data: Set[TidalTrack] = set()

    def __new__(cls, playlist: TidalPlaylist):
        if cls._existing is None:
            cls._existing = super().__new__(cls)
            cls._data = set()

        cls._data.update(playlist.tracks())
        return cls._existing

    def _search(self, spotify_track: SpotifyTrack) -> TidalTrack | None:
        """check if the given spotify track was already in the tidal playlist."""
        return next(filter(lambda x: match(x, spotify_track), self._data), None)

    def search(
        self, spotify_session: spotipy.Spotify, spotify_playlist
    ) -> Tuple[List[Tuple[SpotifyTrack, TidalTrack | None]], int]:
        """Add the cached tidal track where applicable to a list of spotify tracks"""
        results = []
        cache_hits = 0
        spotify_tracks = get_tracks_from_spotify_playlist(
            spotify_session, spotify_playlist
        )
        for track in spotify_tracks:
            cached_track = self._search(track)
            if cached_track is not None:
                cache_hits += 1
            results.append((track, cached_track))
        return (results, cache_hits)


def tidal_playlist_is_dirty(
    playlist: TidalPlaylist, new_track_ids: List[TidalID]
) -> bool:
    old_tracks = playlist.tracks()
    if len(old_tracks) != len(new_track_ids):
        return True
    for i in range(len(old_tracks)):
        if old_tracks[i].id != new_track_ids[i]:
            return True
    return False


def sync_playlist(
    spotify_session: SpotifySession,
    tidal_session: TidalSession,
    spotify_id: SpotifyID,
    config,
    tidal_id: Optional[TidalID] = None,
):
    try:
        spotify_playlist = spotify_session.playlist(spotify_id)
    except spotipy.SpotifyException as e:
        logger.error("Error getting Spotify playlist %s", spotify_id)
        logger.exception(e)
        return
    if tidal_id:
        # if a Tidal playlist was specified then look it up
        try:
            tidal_playlist = tidal_session.playlist(tidal_id)
        except Exception as e:
            logger.warning("Error getting Tidal playlist %s", tidal_id)
            logger.debug(e)
            return
    else:
        # create a new Tidal playlist if required
        logger.warn(
            "No playlist found on Tidal corresponding to Spotify playlist: %s. Creating new playlist",
            spotify_playlist["name"],
        )
        tidal_playlist: TidalPlaylist = tidal_session.user.create_playlist(
            spotify_playlist["name"], spotify_playlist["description"]
        )
    tidal_track_ids = []
    spotify_tracks, cache_hits = TidalPlaylistCache(tidal_playlist).search(
        spotify_session, spotify_playlist
    )
    if cache_hits == len(spotify_tracks):
        logger.warn(
            "No new tracks to search in Spotify playlist '%s'", spotify_playlist["name"]
        )
        return

    task_description = (
        "Searching Tidal for {}/{} tracks in Spotify playlist '{}'".format(
            len(spotify_tracks) - cache_hits,
            len(spotify_tracks),
            spotify_playlist["name"],
        )
    )
    tidal_tracks = call_async_with_progress(
        tidal_search,
        spotify_tracks,
        task_description,
        config.get("subprocesses", 25),
        tidal_session=tidal_session,
    )

    missing_tracks = 0
    for index, tidal_track in enumerate(tidal_tracks):
        spotify_track = spotify_tracks[index][0]
        if tidal_track:
            tidal_track_ids.append(tidal_track.id)
        else:
            missing_tracks += 1
            color = ("\033[91m", "\033[0m")
            logger.info(
                color[0] + "Could not find track %s: %s - %s" + color[1],
                spotify_track["id"],
                ",".join(map(lambda x: x["name"], spotify_track["artists"])),
                spotify_track["name"],
            )
    logger.warn("Could not find %d tracks in Tidal", missing_tracks)
    if tidal_playlist_is_dirty(tidal_playlist, tidal_track_ids):
        set_tidal_playlist(tidal_playlist, tidal_track_ids)
        print("Synced playlist.")
    else:
        print("No changes to write to Tidal playlist")


def update_tidal_playlist(
    playlist: TidalPlaylist, track_ids: List[TidalID], *, chunk_size: int = 20
) -> None:
    offset = 0
    with tqdm(
        desc="Erasing existing tracks from Tidal playlist", total=playlist.num_tracks
    ) as progress:
        while playlist.num_tracks:
            indices = range(offset, min(playlist.num_tracks, chunk_size + offset))
            try:
                print(playlist._etag)
                playlist.remove_by_indices(indices)
                offset += chunk_size
            except:
                logger.info("412 hit, sleeping for a bit.")
                time.sleep(0.5)
                continue
            progress.update(len(indices))
    with tqdm(
        desc="Adding new tracks to Tidal playlist", total=len(track_ids)
    ) as progress:
        offset = 0
        while offset < len(track_ids):
            count = min(chunk_size, len(track_ids) - offset)
            playlist.add(track_ids[offset : offset + chunk_size])
            offset += count
            progress.update(count)


def sync_list(
    spotify_session: spotipy.Spotify,
    tidal_session: tidalapi.Session,
    playlists: List[PlaylistConfig],
    config: SyncConfig,
) -> List[TidalID]:
    results = []
    for spotify_id, tidal_id in playlists:
        # sync the spotify playlist to tidal
        repeat_on_request_error(
            sync_playlist, spotify_session, tidal_session, spotify_id, config, tidal_id
        )
        results.append(tidal_id)
    return results
