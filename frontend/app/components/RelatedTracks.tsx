'use client';

import { useMemo, useState } from 'react';
import useSWR from 'swr';
import Image from 'next/image';
import Link from 'next/link';
import { fetcher } from '@/app/lib/fetcher';
import type { RelatedTrack, RelatedTracksResponse } from '@/app/types';

interface RelatedTracksProps {
  trackId: string;
  /** Artist of the page's track — used to power the same-artist toggle. */
  artistId: string | null;
}

/**
 * Shows tracks frequently played in the same listening session as the current
 * track. Fetched client-side so it doesn't block the initial server render.
 * The same-artist toggle is a pure client-side filter — no extra requests.
 */
export default function RelatedTracks({ trackId, artistId }: RelatedTracksProps) {
  const [hideSameArtist, setHideSameArtist] = useState(false);

  const { data, error, isLoading } = useSWR<RelatedTracksResponse>(
    `/api/tracks/${trackId}/related?limit=20`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const visible = useMemo(() => {
    if (!data) return [];
    if (!hideSameArtist || !artistId) return data.related;
    return data.related.filter((r) => r.artist_id !== artistId);
  }, [data, hideSameArtist, artistId]);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4 gap-4">
        <h2 className="font-lexend text-xl sm:text-2xl font-normal">
          Often played together
        </h2>
        {artistId && data && data.related.length > 0 && (
          <label className="font-lexend font-light text-sm text-muted flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={hideSameArtist}
              onChange={(e) => setHideSameArtist(e.target.checked)}
              className="cursor-pointer"
            />
            Hide same artist
          </label>
        )}
      </div>

      {error ? (
        <p className="font-lexend font-light text-danger">
          Failed to load related tracks.
        </p>
      ) : isLoading ? (
        <p className="font-lexend font-light text-soft">Loading…</p>
      ) : visible.length === 0 ? (
        <p className="font-lexend font-light text-soft">
          {data && data.related.length > 0
            ? 'No related tracks from other artists.'
            : 'Not enough listening history yet to find related tracks.'}
        </p>
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-3 -mx-4 sm:-mx-6 md:-mx-8 px-4 sm:px-6 md:px-8 snap-x snap-proximity scroll-pl-4 sm:scroll-pl-6 md:scroll-pl-8 scroll-row-fade">
          {visible.map((r) => (
            <RelatedCard key={r.track_id} track={r} />
          ))}
        </div>
      )}
    </div>
  );
}

function RelatedCard({ track }: { track: RelatedTrack }) {
  // Display the cosine similarity score so the visible number matches the
  // sort order. Cosine penalizes tracks that are popular everywhere, so a
  // track only counts as "highly related" when it co-occurs with the target
  // *and* doesn't appear in many other sessions on its own.
  const score = Math.round(track.score * 100);
  const sessionLabel = `${track.cooc_sessions} session${track.cooc_sessions === 1 ? '' : 's'}`;
  const conditionalPct = Math.round(track.cooc_rate * 100);
  const songHref = `/songs/${track.track_id}`;

  return (
    <div className="rounded-lg font-lexend font-light w-32 sm:w-40 shrink-0 snap-start">
      <Link
        href={songHref}
        className="flex flex-col items-start hover:opacity-80 transition-opacity"
      >
        <div className="relative w-32 h-32 sm:w-40 sm:h-40 mb-3">
          {track.album_image ? (
            <Image
              src={track.album_image}
              alt={track.track_name}
              width={160}
              height={160}
              className="w-full h-full object-cover rounded-md"
            />
          ) : (
            <div className="w-full h-full bg-soft/20 rounded-md" />
          )}
          <span
            className="absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded-md bg-black/70 text-white text-xs font-normal tabular-nums"
            title={`Similarity ${score}/100 — co-occurred in ${sessionLabel} (${conditionalPct}% of your sessions with this track)`}
          >
            {score}
          </span>
        </div>

        <div className="w-full">
          <div className="font-normal text-sm whitespace-normal wrap-break-word">
            {track.track_name}
          </div>
        </div>
      </Link>

      <div className="text-muted text-xs mt-2 whitespace-normal wrap-break-word">
        {track.artist_id ? (
          <Link
            href={`/artists/${track.artist_id}`}
            className="hover:text-foreground transition-colors"
          >
            {track.artist_name}
          </Link>
        ) : (
          track.artist_name
        )}
        {' • '}
        {sessionLabel}
      </div>
    </div>
  );
}
