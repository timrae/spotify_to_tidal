#!/usr/bin/env python3

import argparse
from auth import open_tidal_session, open_spotify_session
from functools import partial
from multiprocessing import Pool
import sys
import spotipy
import tidalapi
import time
from tqdm import tqdm
from unidecode import unidecode
from urllib.parse import urljoin
import webbrowser
import yaml

def simple(input_string):
    # only take the first part of a string before any hyphens or brackets to account for different versions
    return unidecode(input_string).split('-')[0].strip().split('(')[0].strip().split('[')[0].strip()

def duration_match(tidal_track, spotify_track, tolerance=2):
    # the duration of the two tracks must be the same to within 2 seconds
    return abs(tidal_track.duration - spotify_track['duration_ms']/1000) < tolerance

def name_match(tidal_track, spotify_track):
    def exclusion_rule(pattern, tidal_track, spotify_track):
        spotify_has_pattern = pattern in spotify_track['name'].lower()
        tidal_has_pattern = pattern in tidal_track.name.lower() or (not tidal_track.version is None and (pattern in tidal_track.version.lower()))
        return spotify_has_pattern != tidal_has_pattern

    # handle some edge cases
    if exclusion_rule("instrumental", tidal_track, spotify_track): return False
    if exclusion_rule("acapella", tidal_track, spotify_track): return False
    if exclusion_rule("remix", tidal_track, spotify_track): return False

    # the simplified version of the Spotify track name must be a substring of the Tidal track name
    simple_spotify_track = simple(spotify_track['name'].lower()).split('feat.')[0].strip()
    return simple_spotify_track in tidal_track.name.lower()

def artist_match(tidal_track, spotify_track):
    def split_artist_name(artist):
       if '&' in artist:
           return artist.split('&')
       elif ',' in artist:
           return artist.split(',')
       else:
           return [artist]

    def get_tidal_artists(tidal_track):
        result = []
        for artist in tidal_track.artists:
            result.extend(split_artist_name(artist.name))
        return set([simple(x.strip().lower()) for x in result])

    def get_spotify_artists(spotify_track):
        result = []
        for artist in spotify_track['artists']:
            result.extend(split_artist_name(artist['name']))
        return set([simple(x.strip().lower()) for x in result])
    # There must be at least one overlapping artist between the Tidal and Spotify track
    return get_tidal_artists(tidal_track).intersection(get_spotify_artists(spotify_track)) != set()

def match(tidal_track, spotify_track):
    return duration_match(tidal_track, spotify_track) and name_match(tidal_track, spotify_track) and artist_match(tidal_track, spotify_track)

def tidal_search(spotify_track_and_cache, tidal_session):
    spotify_track, cached_tidal_track = spotify_track_and_cache
    if cached_tidal_track: return cached_tidal_track
    # search for album name and first album artist
    if 'album' in spotify_track and 'artists' in spotify_track['album'] and len(spotify_track['album']['artists']):
        album_result = tidal_session.search('album', simple(spotify_track['album']['name']) + " " + simple(spotify_track['album']['artists'][0]['name']))
        for album in album_result.albums:
            album_tracks = tidal_session.get_album_tracks(album.id)
            if len(album_tracks) >= spotify_track['track_number']:
                track = album_tracks[spotify_track['track_number'] - 1]
                if match(track, spotify_track):
                    return track
    # if that fails then search for track name and first artist
    for track in tidal_session.search('track', simple(spotify_track['name']) + ' ' + simple(spotify_track['artists'][0]['name'])).tracks:
        if match(track, spotify_track):
            return track

def get_tidal_playlists_dict(tidal_session):
    # a dictionary of name --> playlist
    tidal_playlists = tidal_session.get_user_playlists(tidal_session.user.id)
    return {playlist.name: playlist for playlist in tidal_playlists}

def set_tidal_playlist(session, playlist_id, track_ids):
    # erases any items in the given playlist, then adds all of the tracks given in track_ids
    # had to hack this together because the API doesn't include it

    chunk_size = 20 # add/delete tracks in chunks of no more than this many tracks
    request_params = {
        'sessionId': session.session_id,
        'countryCode': session.country_code,
        'limit': '999',
    }
    def get_headers():
        etag = session.request('GET','playlists/%s/tracks' % playlist_id).headers['ETag']
        return {'if-none-match' : etag}

    # clear all old items from playlist
    playlist = session.get_playlist(playlist_id)
    progress = tqdm(desc="Erasing existing tracks from Tidal playlist", total=playlist.num_tracks)
    while True:
        if not playlist.num_tracks:
            break
        track_index_string = ",".join([str(x) for x in range(min(chunk_size, playlist.num_tracks))])
        result = session.request('DELETE', 'playlists/{}/tracks/{}'.format(playlist.id, track_index_string), params=request_params, headers=get_headers())
        result.raise_for_status()
        progress.update(min(chunk_size, playlist.num_tracks))
        playlist = session.get_playlist(playlist_id)
    progress.close()

    # add all new items to the playlist
    offset = 0
    progress = tqdm(desc="Adding new tracks to Tidal playlist", total=len(track_ids))
    while offset < len(track_ids):
        count = min(chunk_size, len(track_ids) - offset)
        data = {
            'trackIds' : ",".join([str(x) for x in track_ids[offset:offset+chunk_size]]),
            'toIndex' : offset
        }
        offset += count
        result = session.request('POST', 'playlists/{}/tracks'.format(playlist.id), params=request_params, data=data, headers=get_headers())
        result.raise_for_status()
        progress.update(count)
    progress.close()

def create_tidal_playlist(session, name):
    result = session.request('POST','users/%s/playlists' % session.user.id ,data={'title': name})
    return session.get_playlist(result.json()['uuid'])

def repeat_on_exception(function, *args, remaining=5, **kwargs):
    # utility to repeat calling the function up to 5 times if an exception is thrown
    try:
        return function(*args, **kwargs)
    except:
        if remaining:
            print("Error, retrying {} more times".format(remaining))
        else:
            print("Repeated error calling the function '{}' with the following arguments:".format(function.__name__))
            print(args)
            raise
        time.sleep(5)
        return repeat_on_exception(function, *args, remaining=remaining-1, **kwargs)

def _enumerate_wrapper(value_tuple, function, **kwargs):
    # just a wrapper which accepts a tuple from enumerate and returns the index back as the first argument
    index, value = value_tuple
    return (index, repeat_on_exception(function, value, **kwargs))

def call_async_with_progress(function, values, description, num_processes, **kwargs):
    results = len(values)*[None]
    with Pool(processes=num_processes) as process_pool:
        for index, result in tqdm(process_pool.imap_unordered(partial(_enumerate_wrapper, function=function, **kwargs),
                                  enumerate(values)), total=len(values), desc=description):
            results[index] = result
    return results

def get_tracks_from_spotify_playlist(spotify_session, spotify_playlist):
    output = []
    results = spotify_session.playlist_tracks(spotify_playlist['id'], fields="next,items(track(name,album(name,artists),artists,track_number,duration_ms,id))")
    while True:
        output.extend([r['track'] for r in results['items'] if r['track'] is not None])
        # move to the next page of results if there are still tracks remaining in the playlist
        if results['next']:
            results = spotify_session.next(results)
        else:
            return output

class TidalPlaylistCache:
    def __init__(self, playlist, tidal_session):
        self._data = tidal_session.get_playlist_tracks(playlist.id)

    def _search(self, spotify_track):
        ''' check if the given spotify track was already in the tidal playlist.'''
        results = []
        for tidal_track in self._data:
            if match(tidal_track, spotify_track):
                return tidal_track
        return None

    def search(self, spotify_session, spotify_playlist):
        ''' Add the cached tidal track where applicable to a list of spotify tracks '''
        results = []
        cache_hits = 0
        work_to_do = False
        spotify_tracks = get_tracks_from_spotify_playlist(spotify_session, spotify_playlist)
        for track in spotify_tracks:
            cached_track = self._search(track)
            if cached_track:
                results.append( (track, cached_track) )
                cache_hits += 1
            else:
                results.append( (track, None) )
        return (results, cache_hits)

def tidal_playlist_is_dirty(tidal_session, playlist_id, new_track_ids):
    old_tracks = tidal_session.get_playlist_tracks(playlist_id)
    if len(old_tracks) != len(new_track_ids):
        return True
    for i in range(len(old_tracks)):
        if old_tracks[i].id != new_track_ids[i]:
            return True
    return False

def sync_playlist(spotify_session, tidal_session, spotify_playlist, tidal_playlist, config):
    tidal_track_ids = []
    spotify_tracks, cache_hits = TidalPlaylistCache(tidal_playlist, tidal_session).search(spotify_session, spotify_playlist)
    if cache_hits == len(spotify_tracks):
        print("No new tracks to search in Spotify playlist '{}'".format(spotify_playlist['name']))
        return

    task_description = "Searching Tidal for {}/{} tracks in Spotify playlist '{}'".format(len(spotify_tracks) - cache_hits, len(spotify_tracks), spotify_playlist['name'])
    tidal_tracks = call_async_with_progress(tidal_search, spotify_tracks, task_description, config.get('subprocesses', 50), tidal_session=tidal_session)
    for index, tidal_track in enumerate(tidal_tracks):
        spotify_track = spotify_tracks[index][0]
        if tidal_track:
            tidal_track_ids.append(tidal_track.id)
        else:
            color = ('\033[91m', '\033[0m')
            print(color[0] + "Could not find track {}: {} - {}".format(spotify_track['id'], ",".join([a['name'] for a in spotify_track['artists']]), spotify_track['name']) + color[1])

    if tidal_playlist_is_dirty(tidal_session, tidal_playlist.id, tidal_track_ids):
        set_tidal_playlist(tidal_session, tidal_playlist.id, tidal_track_ids)
    else:
        print("No changes to write to Tidal playlist")

def sync_list(spotify_session, tidal_session, playlists, config):
    results = []
    tidal_playlists = get_tidal_playlists_dict(tidal_session)
    for spotify_id, tidal_id in playlists:
        try:
            spotify_playlist = spotify_session.playlist(spotify_id)
        except spotipy.SpotifyException as e:
            print("Error getting Spotify playlist " + spotify_id)
            print(e)
            results.append(None)
            continue
        if tidal_id:
            # if the user manually specified the id of a Tidal playlist to use then favour that
            try:
                tidal_playlist = tidal_session.get_playlist(tidal_id)
            except exception:
                print("Error getting Tidal playlist " + tidal_id)
                print(e)
                continue
        elif spotify_playlist['name'] in tidal_playlists:
            # if there's an existing tidal playlist with the name of the current playlist then use that
            tidal_playlist = tidal_playlists[spotify_playlist['name']]
        else:
            # otherwise create a new playlist
            tidal_playlist = create_tidal_playlist(tidal_session, spotify_playlist['name'])
        repeat_on_exception(sync_playlist, spotify_session, tidal_session, spotify_playlist, tidal_playlist, config)
        results.append(tidal_playlist)
    return results

def get_playlists_from_spotify(spotify_session, config):
    # get all the user playlists from the Spotify account
    playlists = []
    spotify_results = spotify_session.user_playlists(config['spotify']['username'])
    exclude_list = set([x.split(':')[-1] for x in config.get('excluded_playlists', [])])
    while True:
        for spotify_playlist in spotify_results['items']:
            if spotify_playlist['owner']['id'] == config['spotify']['username'] and not spotify_playlist['id'] in exclude_list:
                playlists.append((spotify_playlist['id'], None))
        # move to the next page of results if there are still playlists remaining
        if spotify_results['next']:
            spotify_results = spotify_session.next(spotify_results)
        else:
            break
    return playlists

def get_playlists_from_config(config):
    # get the list of playlist sync mappings from the configuration file
    return [(item['spotify_id'], item['tidal_id']) for item in config['sync_playlists']]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config.yml', help='location of the config file')
    parser.add_argument('--uri', help='synchronize a specific URI instead of the one in the config')
    parser.add_argument('--session-dir', default='./', help='location of where session files are stored')
    args = parser.parse_args()

    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)
    spotify_session = open_spotify_session(config['spotify'], args.session_dir)
    tidal_session = open_tidal_session(args.session_dir)
    if not tidal_session.check_login():
        sys.exit("Could not connect to Tidal")
    if args.uri:
        # if a playlist ID is explicitly provided as a command line argument then use that
        sync_list(spotify_session, tidal_session, [(args.uri, None)], config)
    elif config.get('sync_playlists', None):
        # if the config contains a sync_playlists list of mappings then use that
        sync_list(spotify_session, tidal_session, get_playlists_from_config(config), config)
    else:
        # otherwise just use the user playlists in the Spotify account
        sync_list(spotify_session, tidal_session, get_playlists_from_spotify(spotify_session, config), config)
