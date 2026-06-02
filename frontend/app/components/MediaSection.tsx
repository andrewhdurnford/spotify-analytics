'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import MediaCard from '@/app/components/MediaCard';
import { pluralize } from '@/app/lib/timeframe';
import type { MediaItem, Song, Artist, Album } from '@/app/types';

interface MediaSectionProps {
  title: string;
  items: MediaItem[];
  type: 'songs' | 'albums' | 'artists';
  timeframe?: string;
  date?: string | null;
}

function buildTimeframeQuery(timeframe?: string, date?: string | null): string {
  if (!timeframe) return '';
  const params = new URLSearchParams({ timeframe });
  if (date) params.set('date', date);
  return `?${params.toString()}`;
}

function getSongHref(item: Song, query: string): string {
  if (item.track_id) {
    return `/songs/${item.track_id}${query}`;
  }
  const encoded = encodeURIComponent(`${item.track_name}--${item.artist_name}`);
  return `/songs/${encoded}${query}`;
}

function getArtistHref(item: Artist, query: string): string | undefined {
  if (item.spotify_id) {
    return `/artists/${item.spotify_id}${query}`;
  }
  return undefined;
}

function getAlbumHref(item: Album, query: string): string | undefined {
  if (!item.artist_id || !item.album_name) return undefined;
  const encoded = encodeURIComponent(`${item.artist_id}--${item.album_name}`);
  return `/albums/${encoded}${query}`;
}

function artistSubtitleLink(
  artistName: string,
  artistId: string | null | undefined,
  query: string,
): ReactNode {
  if (!artistName) return null;
  if (artistId) {
    return (
      <Link
        href={`/artists/${artistId}${query}`}
        className="hover:text-foreground transition-colors"
      >
        {artistName}
      </Link>
    );
  }
  return <span>{artistName}</span>;
}

export default function MediaSection({ title, items, type, timeframe, date }: MediaSectionProps) {
  if (items.length === 0) {
    return (
      <div>
        <h2 className="font-lexend text-2xl font-normal mb-4">{title}</h2>
        <p className="font-lexend font-light text-soft">
          No data available for this period
        </p>
      </div>
    );
  }

  const query = buildTimeframeQuery(timeframe, date);

  const getItemName = (item: MediaItem) => {
    if (type === 'songs' && 'track_name' in item) return item.track_name;
    if (type === 'albums' && 'album_name' in item) return item.album_name;
    return item.artist_name;
  };

  const getImageSrc = (item: MediaItem) => {
    if ('album_image' in item && item.album_image) return item.album_image;
    if ('image_url' in item && item.image_url) return item.image_url;
    return null;
  };

  const getSubtitle = (item: MediaItem): ReactNode => {
    const count = item.play_count ?? 0;
    const plays = `${count} ${pluralize(count, 'play')}`;
    if (type === 'artists') return plays;

    const artistId =
      (type === 'songs' && 'artist_id' in item ? (item as Song).artist_id : null) ??
      (type === 'albums' && 'artist_id' in item ? (item as Album).artist_id : null);

    return (
      <>
        {plays}
        {' • '}
        {artistSubtitleLink(item.artist_name, artistId, query)}
      </>
    );
  };

  const getHref = (item: MediaItem): string | undefined => {
    if (type === 'songs' && 'track_name' in item) {
      return getSongHref(item as Song, query);
    }
    if (type === 'artists' && 'artist_name' in item) {
      return getArtistHref(item as Artist, query);
    }
    if (type === 'albums' && 'album_name' in item) {
      return getAlbumHref(item as Album, query);
    }
    return undefined;
  };

  return (
    <div>
      <h2 className="font-lexend text-xl sm:text-2xl font-normal mb-4">{title}</h2>
      <div className="flex gap-3 overflow-x-auto pb-3 -mx-4 sm:-mx-6 md:-mx-8 px-4 sm:px-6 md:px-8 snap-x snap-proximity scroll-pl-4 sm:scroll-pl-6 md:scroll-pl-8 scroll-row-fade">
        {items.map((item, idx) => (
          <MediaCard
            key={idx}
            name={getItemName(item) || 'Unknown'}
            subtitle={getSubtitle(item)}
            imageSrc={getImageSrc(item)}
            href={getHref(item)}
          />
        ))}
      </div>
    </div>
  );
}
