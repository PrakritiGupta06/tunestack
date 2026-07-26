"""
Builds the content-based recommender.

Design note: a full N x N similarity matrix at N=5000 is a ~200MB dense
float64 array. Nothing that queries this model ever needs the full matrix --
a /recommend endpoint only ever asks "top-K most similar to track X". So this
computes the full matrix in memory (fine, transient), then keeps only the
top-K neighbors per track for the saved artifact and throws the rest away.
"""
import re
import pickle
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics.pairwise import cosine_similarity

N_TRACKS = 5000
TOP_K = 30
MAX_PER_ARTIST = 2  # see note below

# genre is coarse (6 values), subgenre is the most specific meaningful
# signal (24 values), artist is included but kept low -- weighting artist
# too heavily just returns more of the same artist instead of "songs like
# this one".
GENRE_WEIGHT = 2
SUBGENRE_WEIGHT = 3
ARTIST_WEIGHT = 1


def clean_token(s):
    """Collapse a category value into a single alnum token so multi-word
    values (e.g. 'hip hop', 'The Weeknd') don't get split into separate
    words by the vectorizer and cross-match on unrelated tracks."""
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def build_soup(row):
    genre = clean_token(row['playlist_genre']) + ' '
    subgenre = clean_token(row['playlist_subgenre']) + ' '
    artist = clean_token(row['track_artist']) + ' '
    return (genre * GENRE_WEIGHT + subgenre * SUBGENRE_WEIGHT + artist * ARTIST_WEIGHT).strip()


def main():
    df = pd.read_csv('spotify_songs.csv')
    df = df.drop_duplicates(subset=['track_name', 'track_artist'])
    df = df.sort_values('track_popularity', ascending=False, kind='stable').head(N_TRACKS)
    df = df.reset_index(drop=True)

    soup = df.apply(build_soup, axis=1)

    vectorizer = CountVectorizer()
    matrix = vectorizer.fit_transform(soup)
    sim = cosine_similarity(matrix)

    neighbors = {}
    for i in range(len(df)):
        row = sim[i]
        ranked_idx = np.argsort(-row, kind='stable')
        selected = []
        artist_counts = {}
        for j in ranked_idx:
            if j == i:
                continue
            cand_artist = df.loc[j, 'track_artist']
            if artist_counts.get(cand_artist, 0) >= MAX_PER_ARTIST:
                continue
            selected.append((df.loc[j, 'track_id'], float(row[j])))
            artist_counts[cand_artist] = artist_counts.get(cand_artist, 0) + 1
            if len(selected) >= TOP_K:
                break
        neighbors[df.loc[i, 'track_id']] = selected

    with open('neighbors.pkl', 'wb') as f:
        pickle.dump(neighbors, f)

    tracks = df[['track_id', 'track_name', 'track_artist', 'playlist_genre',
                 'playlist_subgenre', 'track_popularity']]
    tracks.to_pickle('tracks.pkl')

    print(f"Built neighbor index for {len(df)} tracks, top-{TOP_K} each")


if __name__ == '__main__':
    main()
