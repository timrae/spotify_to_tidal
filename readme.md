A command line tool for importing your Spotify playlists into Tidal

Installation
-----------
Clone this git repository and then run:

```bash
python3 -m pip install -e .
spotify_to_tidal -h
```

Setup
-----
0. Rename the file example_config.yml to config.yml
0. Go [here](https://developer.spotify.com/documentation/general/guides/authorization/app-settings/) and register a new app on developer.spotify.com.
0. Copy and paste your client ID and client secret to the Spotify part of the config file
0. Copy and paste the value in 'redirect_uri' of the config file to Redirect URIs at developer.spotify.com and press ADD
0. Enter your Spotify username to the config file

Usage
----
To synchronize all of your Spotify playlists with your Tidal account run the following

```bash
pip install -e .
spotify_to_tidal
python3 sync.py
```

This will take a long time because the Tidal API is really slow.

You can also just synchronize a specific playlist by doing the following:

```bash
spotify_to_tidal --uri 1ABCDEqsABCD6EaABCDa0a
```

See example_config.yml for more configuration options, and `sync.py --help` for more options.
