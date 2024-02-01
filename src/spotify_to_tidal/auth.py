#!/usr/bin/env python3
import logging
import sys

from .type import *
from typing import Union, NoReturn, Optional
import spotipy
import tidalapi
import webbrowser
import yaml

logger = logging.getLogger(__name__)


def open_spotify_session(
    *, username: str, client_id: str, client_secret: str, redirect_uri: str
) -> Union[spotipy.Spotify, NoReturn]:
    credentials_manager = spotipy.SpotifyOAuth(
        username=username,
        scope="playlist-read-private",
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )
    try:
        credentials_manager.get_access_token(as_dict=False)
    except spotipy.SpotifyOauthError:
        logger.critical(
            "Error opening Spotify sesion; could not get token for username: %s",
            username,
        )
        sys.exit(1)

    return spotipy.Spotify(oauth_manager=credentials_manager)


def open_tidal_session(config: Optional[tidalapi.Config] = None) -> tidalapi.Session:
    try:
        with open(".session.yml", "r") as session_file:
            previous_session: TidalConfig = yaml.safe_load(session_file)
    except OSError:
        previous_session = None

    if config:
        session = tidalapi.Session(config=config)
    else:
        session = tidalapi.Session()
    if previous_session:
        try:
            if session.load_oauth_session(
                token_type=previous_session["token_type"],
                access_token=previous_session["access_token"],
                refresh_token=previous_session["refresh_token"],
            ):
                return session
        except Exception as e:
            logger.warn("Error loading previous Tidal Session")
            logger.debug(e)

    if not session.check_login():
        logging.critical("Could not connect to Tidal")
        sys.exit(1)

    login, future = session.login_oauth()
    print("Login with the webbrowser: " + login.verification_uri_complete)
    url = login.verification_uri_complete
    if not url.startswith("https://"):
        url = "https://" + url
    webbrowser.open(url)
    future.result()

    if not session.check_login():
        logging.critical("Could not connect to Tidal")
        sys.exit(1)
    with open(".session.yml", "w") as f:
        yaml.dump(
            {
                "session_id": session.session_id,
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
            },
            f,
        )
    return session
