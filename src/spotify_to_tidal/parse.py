from typing import Any, Dict, List, Tuple, Callable, Optional, Set
from .type import *


def get_tidal_playlists_dict(tidal_session: TidalSession) -> Dict[str, TidalPlaylist]:
    # a dictionary of name --> playlist
    tidal_playlists = tidal_session.user.playlists()
    output = {}
    for playlist in tidal_playlists:
        output[playlist.name] = playlist
    return output


def playlist_id_tuple(
    spotify_playlist: Dict[str, Any], tidal_playlists: Dict[str, TidalPlaylist]
) -> Tuple[SpotifyID, TidalID | None]:
    if spotify_playlist["name"] in tidal_playlists:
        # if there's an existing tidal playlist with the name of the current playlist then use that
        tdl_playlist_id = tidal_playlists[spotify_playlist["name"]].id
    else:
        tdl_playlist_id = None
    return (spotify_playlist["id"], tdl_playlist_id)


def create_playlist_id_tuple(
    spotify_session: SpotifySession,
    tidal_session: TidalSession,
    exclude: Optional[List[str]] = None,
    uname: Optional[str] = None,
) -> List[Tuple[SpotifyID, TidalID | None]]:
    spotify_playlists = get_spotify_playlists(
        spotify_session, exclude=exclude, uname=uname
    )
    tidal_playlists = get_tidal_playlists_dict(tidal_session)

    return [
        playlist_id_tuple(spotify_playlist, tidal_playlists)
        for spotify_playlist in spotify_playlists
    ]


def get_spotify_playlists(
    spotify_session: SpotifySession,
    exclude: Optional[Set[str]] = None,
    uname: Optional[str] = None,
):
    # get all the user playlists from the Spotify account
    playlists = []
    if exclude is None:
        exclude = []
    if uname is None:
        spotify_results = spotify_session.current_user_playlists()
        uname = spotify_session.current_user()["id"]
    else:
        spotify_results = spotify_session.user_playlists(uname)
    print(exclude)
    exclude_list = set(map(str.split(":")[-1], exclude))

    def condition(playlist: Dict[str, Any]) -> bool:
        return playlist["owner"]["id"] == uname and playlist["id"] not in exclude_list

    while True:
        playlists.extend(filter(condition, spotify_results["items"]))
        # move to the next page of results if there are still playlists remaining
        if spotify_results["next"]:
            spotify_results = spotify_session.next(spotify_results)
        else:
            break
    return playlists


def get_playlists_from_config(
    config: SyncConfig,
) -> List[Tuple[SpotifyID, TidalID]]:
    # get the list of playlist sync mappings from the configuration file
    return list(
        map(
            lambda x: (x["spotify_id"], x["tidal_id"]), config.get("sync_playlists", [])
        )
    )
