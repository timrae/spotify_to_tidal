import unicodedata
from .type import *
from typing import List
import logging
from itertools import chain

pkg_logger = logging.getLogger(__package__)
logger = logging.getLogger(__name__)
for hndl in pkg_logger.handlers:
    logger.addHandler(hndl)


def normalize(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")


def simple(input_string: str) -> str:
    # only take the first part of a string before any hyphens or brackets to account for different versions
    return (
        input_string.split("-")[0].strip().split("(")[0].strip().split("[")[0].strip()
    )


def isrc_match(tidal_track: TidalTrack, spotify_track: SpotifyTrack) -> bool:
    if "isrc" in spotify_track["external_ids"]:
        return tidal_track.isrc == spotify_track["external_ids"]["isrc"]
    return False


def duration_match(
    tidal_track: TidalTrack, spotify_track: SpotifyTrack, tolerance=2
) -> bool:
    # the duration of the two tracks must be the same to within 2 seconds
    return abs(tidal_track.duration - spotify_track["duration_ms"] / 1000) < tolerance


def name_match(tidal_track: TidalTrack, spotify_track: SpotifyTrack) -> bool:
    def exclusion_rule(pattern: str):
        spotify_has_pattern = pattern in spotify_track["name"].casefold()
        tidal_has_pattern = pattern in tidal_track.name.casefold() or (
            not tidal_track.version is None
            and (pattern in tidal_track.version.casefold())
        )
        return spotify_has_pattern or tidal_has_pattern

    # handle some edge cases
    if exclusion_rule("instrumental"):
        return False
    if exclusion_rule("acapella"):
        return False
    if exclusion_rule("remix"):
        return False

    # the simplified version of the Spotify track name must be a substring of the Tidal track name
    # Try with both un-normalized and then normalized
    simple_spotify_track = simple(spotify_track["name"].casefold())
    if "feat." in simple_spotify_track:
        kw = ".feat"
    elif "ft." in simple_spotify_track:
        kw = ".ft"
    else:
        kw = ""
    if kw != "":
        simple_spotify_track = simple_spotify_track.split(kw)[0].strip()
    return simple_spotify_track in tidal_track.name.casefold() or normalize(
        simple_spotify_track
    ) in normalize(tidal_track.name.casefold())


def artist_match(tidal_track: TidalTrack, spotify_track: SpotifyTrack) -> bool:
    def split_artist_name(artist: str) -> List[str]:
        if "&" in artist:
            return artist.split("&")
        elif "," in artist:
            return artist.split(",")
        else:
            return [artist]

    def split_and_clean(artist: str) -> List[str]:
        return map(lambda x: x.strip().casefold(), split_artist_name(artist))

    spotify_artists = set(
        chain.from_iterable(
            map(lambda x: split_and_clean(x["name"]), spotify_track["artists"])
        )
    )
    spotify_artists_normalized = set(map(normalize, spotify_artists))
    tidal_artists = chain(map(lambda x: x.name, tidal_track.artists))
    tidal_artists = set(chain.from_iterable(map(split_and_clean, tidal_artists)))
    tidal_artists_normalized = set(map(normalize, tidal_artists))

    return tidal_artists.intersection(
        spotify_artists
    ) or tidal_artists_normalized.intersection(spotify_artists_normalized)


def match(tidal_track, spotify_track) -> bool:
    return isrc_match(tidal_track, spotify_track) or (
        duration_match(tidal_track, spotify_track)
        and name_match(tidal_track, spotify_track)
        and artist_match(tidal_track, spotify_track)
    )


class FilterOtherPkgs(logging.Filter):
    def filter(self, record: logging.LogRecord):
        return (
            record.module.split(".")[0] != __package__
            and record.levelno <= logging.DEBUG
        )


class Filter429(logging.Filter):
    def filter(self, record: logging.LogRecord):
        return (
            record.getMessage() == "HTTP error on 429"
            or record.getMessage() == "HTTP error on 412"
            or not record.module.startswith(__package__)
        )
