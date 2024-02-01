import logging
import argparse
import sys
import yaml
from pathlib import Path
from .filters import Filter429
from .auth import open_tidal_session, open_spotify_session
from .parse import (
    get_tidal_playlists_dict,
    playlist_id_tuple,
    create_playlist_id_tuple,
)
from .sync import sync_list
from .type import SyncConfig, SpotifyConfig

from typing import NoReturn


def setup_args() -> argparse.ArgumentParser:
    synopsis = """
Syncs spotify playlists to Tidal. Can specify a config yaml or specify Spotify Oauth values on the command line.

"""
    parser = argparse.ArgumentParser(description=synopsis)

    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Location of the spotify to tidal config file",
        dest="config",
    )
    parser.add_argument(
        "-i", "--id", type=str, help="Spotify client ID", dest="client_id"
    )
    parser.add_argument(
        "-s", "--secret", type=str, help="Spotify client secret", dest="client_secret"
    )
    parser.add_argument(
        "-U",
        "--username",
        type=str,
        help="Spotify username of the Oauth creds",
        dest="spotify_uname",
        default=None,
    )
    parser.add_argument(
        "-u",
        "--uri",
        help="Synchronize specific URI(s) instead of the one in the config",
        nargs='*',
        dest="uri",
    )
    parser.add_argument(
        "-v",
        help="verbosity level, increases per v (max of 3) specified",
        default=0,
        action="count",
        dest="verbosity",
    )
    parser.add_argument(
        "-a",
        "--all",
        help="Sync all spotify playlists to Tidal. This will not honor the config's exclude list.",
        action="store_true",
        default=False,
        dest="all_playlists",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        help="ID of Spotify playlists to exclude",
        nargs="*",
        dest="exclude_ids",
        type=set,
    )

    return parser


logger = None


def setup_logging(verbosity: int) -> None:
    log_map = [logging.WARNING, logging.INFO, logging.DEBUG]
    strm_hndl = logging.StreamHandler(sys.stdout)
    logging.root.addFilter(Filter429("tidalapi"))
    logging.root.addFilter(Filter429("spotipy"))
    global logger
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(module)s:%(funcName)s:%(lineno)d - %(message)s"
    )
    strm_hndl.setFormatter(fmt)
    logger = logging.getLogger(__package__)
    logger.setLevel(log_map[min(verbosity, 2)])
    logger.addHandler(strm_hndl)
    logger.debug("Initialized logging.")


def parse_args(parser: argparse.ArgumentParser) -> argparse.Namespace | NoReturn:
    args = parser.parse_args()
    setup_logging(args.verbosity)
    if args.config is None:
        logging.debug("No config specified, checking other args.")
        if args.client_id is None:
            raise RuntimeError(
                "No config specified and Spotify client ID not specified."
            )
        if args.client_secret is None:
            raise RuntimeError(
                "No config specified and Spotify secret ID not specified."
            )
        if args.spotify_uname is None:
            raise RuntimeError(
                "No config specified and Spotify username not specified."
            )
    if args.config and any(
        x is not None for x in (args.client_id, args.client_secret, args.spotify_uname)
    ):
        raise RuntimeError(
            "Config specfied with config attributes. Only specify a config or all attributes."
        )
    if args.exclude_ids is None:
        args.exclude_ids = set()
    # TODO: more validation?
    return args


def main():
    parser = setup_args()
    args = parse_args(parser)
    if args.config:
        with open(args.config, "r") as f:
            config: SyncConfig = yaml.safe_load(f)
        args.exclude_ids.update(*config.get("excluded_playlists", []))
        spotify_cfg: SpotifyConfig = config.get("spotify", {})
        args.spotify_uname = spotify_cfg.get("username")
        args.client_secret = spotify_cfg.get("client_secret")
        args.client_id = spotify_cfg.get("client_id")
        args.username = spotify_cfg.get("username")
        redirect_uri = spotify_cfg.get("redirect_uri", "http://localhost:8888/callback")
    else:
        args.config = {}
        redirect_uri = "http://localhost:8888/callback"
    spotify_session = open_spotify_session(
        username=args.username,
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=redirect_uri,
    )
    tidal_session = open_tidal_session()
    tidal_playlists = get_tidal_playlists_dict(tidal_session)
    id_tuples = []
    if args.uri:
        # if a playlist ID is explicitly provided as a command line argument then use that
        for uri in args.uri:
            spotify_playlist = spotify_session.playlist(uri)
            id_tuples.append(playlist_id_tuple(spotify_playlist, tidal_playlists))
    elif args.config and (x := config.get("sync_playlists", [])):
        for ids in x:
            id_tuples.append((ids["spotify_id"], ids.get("tidal_id")))
    elif args.all_playlists or config.get("sync_playlists", None):
        id_tuples = create_playlist_id_tuple(
            spotify_session, tidal_session, args.exclude_ids
        )
        # sync_list(spotify_session, tidal_session, id_map, config)
    logger.info("Syncing %d playlists", len(id_tuples))

    sync_list(
        spotify_session,
        tidal_session,
        id_tuples,
        config,
    )
