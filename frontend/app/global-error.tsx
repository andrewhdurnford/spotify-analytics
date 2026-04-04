'use client';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="bg-[#001A23]">
        <main className="min-h-screen flex flex-col items-center justify-center text-[#F4F4F9] p-8">
          <h2 className="font-['lexend'] text-2xl font-normal mb-4">Something went wrong</h2>
          <p className="font-['lexend'] font-light text-[#A1A1A7] mb-6 text-center max-w-md">
            An unexpected error occurred. Please try again.
          </p>
          <button
            onClick={reset}
            className="font-['lexend'] font-light text-sm text-[#F4F4F9] underline hover:text-white cursor-pointer"
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
