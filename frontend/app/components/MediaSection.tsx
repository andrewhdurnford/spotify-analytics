'use client';

import MediaCard from '@/app/components/MediaCard';
import type { MediaItem, Song, Artist } from '@/app/types';

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
  // Fallback: encode trackName--artistName
  const encoded = encodeURIComponent(`${item.track_name}--${item.artist_name}`);
  return `/songs/${encoded}${query}`;
}

function getArtistHref(item: Artist, query: string): string | undefined {
  if (item.spotify_id) {
    return `/artists/${item.spotify_id}${query}`;
  }
  return undefined;
}

export default function MediaSection({ title, items, type, timeframe, date }: MediaSectionProps) {
  if (items.length === 0) {
    return (
      <div>
        <h2 className="font-['lexend'] text-2xl font-normal mb-4">{title}</h2>
        <p className="font-['lexend'] font-light text-[#D1D1D7]">
          No data available for this period
        </p>
      </div>
    );
  }

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

  const getSubtitle = (item: MediaItem) => {
    const plays = `${item.play_count ?? 0} plays`;
    if (type === 'artists') return plays;
    return `${plays}\u00A0\u2022\u00A0${item.artist_name}`;
  };

  const getHref = (item: MediaItem): string | undefined => {
    const query = buildTimeframeQuery(timeframe, date);
    if (type === 'songs' && 'track_name' in item) {
      return getSongHref(item as Song, query);
    }
    if (type === 'artists' && 'artist_name' in item) {
      return getArtistHref(item as Artist, query);
    }
    // Albums don't have detail pages
    return undefined;
  };

  return (
    <div>
      <h2 className="font-['lexend'] text-xl sm:text-2xl font-normal mb-4">{title}</h2>
      <div className="flex gap-3 overflow-x-auto pb-3 -mx-4 sm:-mx-6 md:-mx-8 px-4 sm:px-6 md:px-8 no-scrollbar-bg">
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
