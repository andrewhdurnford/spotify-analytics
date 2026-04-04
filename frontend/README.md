# Spotify Analytics Frontend

Next.js webapp to see my Spotify listening statistics. See my top songs/albums/artists throughout the years and what I'm currently listening to.


## Tech Stack

- **Next.js 16** - React framework with App Router
- **TypeScript** - Type-safe development
- **Tailwind CSS** - Utility-first styling
- **Axios** - HTTP client for API requests

## API Integration

The frontend communicates with the FastAPI backend at the following endpoints:

- `GET /api/top/songs` - Top songs with timeframe filtering
- `GET /api/top/artists` - Top artists with timeframe filtering
- `GET /api/top/albums` - Top albums with timeframe filtering
- `GET /api/top/available-periods` - Available years and date ranges
- `GET /api/top/currently-playing` - Currently playing track (polled every 5 seconds)
