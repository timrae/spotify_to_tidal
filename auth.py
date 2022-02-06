#!/usr/bin/env python3

import os
import sys
import spotipy
import tidalapi
import webbrowser
import yaml

def open_spotify_session(config, session_dir):
    cache_path = os.path.join(session_dir, '.cache-' + config['username'])
    credentials_manager = spotipy.SpotifyOAuth(username=config['username'],
				       scope='playlist-read-private',
				       client_id=config['client_id'],
				       client_secret=config['client_secret'],
				       redirect_uri=config['redirect_uri'],
                       cache_path=cache_path)
    try:
        credentials_manager.get_access_token(as_dict=False)
    except spotipy.SpotifyOauthError:
        sys.exit("Error opening Spotify sesion; could not get token for username: ".format(config['username']))

    return spotipy.Spotify(oauth_manager=credentials_manager)

def open_tidal_session(session_dir):
    session_filepath = os.path.join(session_dir, '.session.yml')
    try:
        with open(session_filepath, 'r') as session_file:
            previous_session = yaml.safe_load(session_file)
    except OSError:
        previous_session = None

    session = tidalapi.Session()
    if previous_session:
        try:
            if session.load_oauth_session(previous_session['session_id'],
                                   previous_session['token_type'],
                                   previous_session['access_token'],
                                   previous_session['refresh_token'] ):
                return session
        except Exception as e:
            print("Error loading previous Tidal Session: \n" + str(e) )

    login, future = session.login_oauth()
    print('Login with the webbrowser: ' + login.verification_uri_complete)
    url = login.verification_uri_complete
    if not url.startswith('https://'):
        url = 'https://' + url
    webbrowser.open(url)
    future.result()
    with open(session_filepath, 'w') as f:
        yaml.dump( {'session_id': session.session_id,
                   'token_type': session.token_type,
                   'access_token': session.access_token,
                   'refresh_token': session.refresh_token}, f )
    return session


