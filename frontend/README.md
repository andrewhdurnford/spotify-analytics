# Spotify Analytics Frontend

Next.js webapp to see my Spotify listening statistics. See my top songs,
albums, artists, recent history, detail pages, and current playback.


## Tech Stack

- **Next.js 15** - React framework with App Router
- **TypeScript** - Type-safe development
- **Tailwind CSS** - Utility-first styling
- **SWR + fetch** - Client data loading through `app/lib/fetcher.ts`

## API Integration

The frontend communicates with the FastAPI backend at the following endpoints:

- `GET /api/top/songs` - Top songs with timeframe filtering
- `GET /api/top/artists` - Top artists with timeframe filtering
- `GET /api/top/albums` - Top albums with timeframe filtering
- `GET /api/top/stats` - Listening totals for the selected timeframe
- `GET /api/top/available-periods` - Available years and date ranges
- `GET /api/top/recently-played` - Paginated listening history
- `GET /api/top/currently-playing` - Currently playing track (polled every 5 seconds)
- `GET /api/artists/{id}` - Artist detail with top songs, albums, and timeline
- `GET /api/tracks/{id}` - Track detail by Spotify ID
- `GET /api/tracks/by-name` - Track detail fallback for streams without Spotify IDs
