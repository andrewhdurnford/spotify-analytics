from datetime import datetime, timedelta

from app.models import Stream
from app.services.ingest import (
    FUZZY_WINDOW,
    ExistingStreamIndex,
    classify_new_records,
    has_fuzzy_match,
    is_skipped_item,
    load_existing_streams,
    parse_spotify_date,
    parse_streaming_records,
    stream_from_record,
)


def test_parse_spotify_date_supports_export_formats():
    assert parse_spotify_date("2025-01-02 03:04") == datetime(2025, 1, 2, 3, 4)
    assert parse_spotify_date("2025-01-02T03:04:05Z") == datetime(2025, 1, 2, 3, 4, 5)
    assert parse_spotify_date("2025-01-02 03:04:05") == datetime(2025, 1, 2, 3, 4, 5)
    assert parse_spotify_date("2025-01-02T03:04:05.123Z") == datetime(2025, 1, 2, 3, 4, 5, 123000)


def test_is_skipped_item_detects_non_completed_export_rows():
    assert is_skipped_item({"reason_end": "fwdbtn"})
    assert is_skipped_item({"reason_end": "logout"})
    assert is_skipped_item({"skipped": True})
    assert is_skipped_item({"is_skipped": 1})
    assert is_skipped_item({"skipped_flag": "yes"})

    assert not is_skipped_item({"reason_end": "trackdone"})
    assert not is_skipped_item({"skipped": False})
    assert not is_skipped_item({"skipped?": "no"})


def test_parse_streaming_records_normalizes_valid_rows_and_counts_skips():
    records, skipped = parse_streaming_records(
        [
            {
                "ts": "2025-01-02T03:04:05Z",
                "master_metadata_track_name": "Song",
                "master_metadata_album_artist_name": "Artist",
                "master_metadata_album_album_name": "Album",
                "ms_played": 123000,
                "spotify_track_uri": "spotify:track:track-1",
                "spotify_artist_uri": "spotify:artist:artist-1",
                "reason_end": "trackdone",
            },
            {
                "ts": "2025-01-02T03:05:05Z",
                "master_metadata_track_name": "Skipped",
                "master_metadata_album_artist_name": "Artist",
                "reason_end": "fwdbtn",
            },
            {
                "master_metadata_track_name": "Missing Timestamp",
                "master_metadata_album_artist_name": "Artist",
            },
            {
                "ts": "bad-date",
                "master_metadata_track_name": "Bad Timestamp",
                "master_metadata_album_artist_name": "Artist",
            },
        ]
    )

    assert skipped == 3
    assert len(records) == 1
    assert records[0].track_name == "Song"
    assert records[0].artist_name == "Artist"
    assert records[0].album_name == "Album"
    assert records[0].played_at == datetime(2025, 1, 2, 3, 4, 5)
    # `ms_played` is intentionally ignored — track length comes from Track
    # metadata enrichment (Spotify API), not from the export row.
    assert records[0].duration_ms == 0
    assert records[0].track_id == "track-1"
    assert records[0].artist_id == "artist-1"


def test_parse_streaming_records_can_require_track_and_artist_names():
    records, skipped = parse_streaming_records(
        [
            {
                "ts": "2025-01-02T03:04:05Z",
                "master_metadata_track_name": None,
                "master_metadata_album_artist_name": "Artist",
            }
        ],
        require_names=True,
    )

    assert records == []
    assert skipped == 1


def test_parse_streaming_records_preserves_legacy_unknown_name_defaults():
    records, skipped = parse_streaming_records(
        [
            {
                "ts": "2025-01-02T03:04:05Z",
                "master_metadata_track_name": None,
                "master_metadata_album_artist_name": None,
            }
        ],
        require_names=False,
    )

    assert skipped == 0
    assert records[0].track_name == "Unknown"
    assert records[0].artist_name == "Unknown"


def test_load_existing_streams_indexes_track_and_name_matches(db):
    played_at = datetime(2025, 1, 2, 3, 4, 5)
    db.add(
        Stream(
            track_name="Song",
            artist_name="Artist",
            album_name="Album",
            played_at=played_at,
            duration_ms=123000,
            track_id="track-1",
            artist_id="artist-1",
        )
    )
    db.commit()

    index = load_existing_streams(db, played_at, played_at)

    assert index.by_track_id == {"track-1": [played_at]}
    assert ("Song", "Artist", played_at) in index.by_name


def test_has_fuzzy_match_uses_configured_window():
    played_at = datetime(2025, 1, 2, 3, 4, 5)
    index = ExistingStreamIndex(
        by_track_id={"track-1": [played_at - FUZZY_WINDOW]},
        by_name=set(),
    )

    assert has_fuzzy_match("track-1", played_at, index)
    assert not has_fuzzy_match("track-2", played_at, index)
    assert not has_fuzzy_match("track-1", played_at + FUZZY_WINDOW + timedelta(seconds=1), index)


def test_classify_new_records_counts_exact_fuzzy_and_name_duplicates():
    records, skipped = parse_streaming_records(
        [
            {
                "ts": "2025-01-02T03:04:05Z",
                "master_metadata_track_name": "Exact",
                "master_metadata_album_artist_name": "Artist",
                "spotify_track_uri": "spotify:track:exact-1",
            },
            {
                "ts": "2025-01-02T03:04:10Z",
                "master_metadata_track_name": "Fuzzy",
                "master_metadata_album_artist_name": "Artist",
                "spotify_track_uri": "spotify:track:fuzzy-1",
            },
            {
                "ts": "2025-01-02T03:04:05Z",
                "master_metadata_track_name": "Name",
                "master_metadata_album_artist_name": "Artist",
            },
            {
                "ts": "2025-01-02T03:04:05Z",
                "master_metadata_track_name": "New",
                "master_metadata_album_artist_name": "Artist",
                "spotify_track_uri": "spotify:track:new-1",
            },
        ],
        require_names=True,
    )
    assert skipped == 0

    index = ExistingStreamIndex(
        by_track_id={
            "exact-1": [datetime(2025, 1, 2, 3, 4, 5)],
            "fuzzy-1": [datetime(2025, 1, 2, 3, 4, 0)],
        },
        by_name={("Name", "Artist", datetime(2025, 1, 2, 3, 4, 5))},
    )

    result = classify_new_records(records, index)

    assert result.dup_exact == 1
    assert result.dup_fuzzy == 1
    assert result.dup_name == 1
    assert [record.track_name for record in result.to_add] == ["New"]


def test_classify_new_records_can_disable_fuzzy_matching():
    records, skipped = parse_streaming_records(
        [
            {
                "ts": "2025-01-02T03:04:10Z",
                "master_metadata_track_name": "Fuzzy",
                "master_metadata_album_artist_name": "Artist",
                "spotify_track_uri": "spotify:track:fuzzy-1",
            }
        ],
        require_names=True,
    )
    assert skipped == 0

    index = ExistingStreamIndex(
        by_track_id={"fuzzy-1": [datetime(2025, 1, 2, 3, 4, 0)]},
        by_name=set(),
    )

    result = classify_new_records(records, index, fuzzy=False)

    assert result.dup_fuzzy == 0
    assert [record.track_name for record in result.to_add] == ["Fuzzy"]


def test_stream_from_record_creates_stream_model():
    records, skipped = parse_streaming_records(
        [
            {
                "ts": "2025-01-02T03:04:05Z",
                "master_metadata_track_name": "Song",
                "master_metadata_album_artist_name": "Artist",
            }
        ]
    )
    assert skipped == 0

    stream = stream_from_record(records[0])

    assert stream.track_name == "Song"
    assert stream.artist_name == "Artist"
    assert stream.played_at == datetime(2025, 1, 2, 3, 4, 5)
