import pickle
import pandas as pd

with open('neighbors.pkl', 'rb') as f:
    NEIGHBORS = pickle.load(f)

TRACKS = pd.read_pickle('tracks.pkl').set_index('track_id')


def recommend(track_name, artist=None, n=5):
    matches = TRACKS[TRACKS['track_name'].str.lower() == track_name.lower()]
    if artist:
        matches = matches[matches['track_artist'].str.lower() == artist.lower()]
    if matches.empty:
        return None
    track_id = matches.index[0]
    neighbor_list = NEIGHBORS.get(track_id, [])[:n]
    return [
        {
            'track_name': TRACKS.loc[nid, 'track_name'],
            'track_artist': TRACKS.loc[nid, 'track_artist'],
            'score': round(score, 3),
        }
        for nid, score in neighbor_list
    ]


if __name__ == '__main__':
    for genre, example in [
        ('pop', 'Blinding Lights'),
        ('rap', 'The Box'),
        ('rock', 'In the End'),
    ]:
        print(f"\n--- Similar to '{example}' ({genre}) ---")
        results = recommend(example)
        if results:
            for r in results:
                print(f"  {r['track_name']} - {r['track_artist']} ({r['score']})")
        else:
            print("  not found")
