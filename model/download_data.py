import urllib.request
import os

URL = "https://raw.githubusercontent.com/rfordatascience/tidytuesday/master/data/2020/2020-01-21/spotify_songs.csv"
OUT = "spotify_songs.csv"

if not os.path.exists(OUT):
    urllib.request.urlretrieve(URL, OUT)
    print(f"Downloaded to {OUT}")
else:
    print(f"{OUT} already exists, skipping")
