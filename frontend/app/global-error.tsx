'use client';

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="bg-deep-blue">
        <main className="min-h-screen flex flex-col items-center justify-center text-foreground p-8">
          <h2 className="font-lexend text-2xl font-normal mb-4">Something went wrong</h2>
          <p className="font-lexend font-light text-muted mb-6 text-center max-w-md">
            An unexpected error occurred. Please try again.
          </p>
          <button
            onClick={reset}
            className="font-lexend font-light text-sm text-foreground underline hover:text-bright cursor-pointer"
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
