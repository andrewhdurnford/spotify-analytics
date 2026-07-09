import Dashboard from '@/app/components/Dashboard';

export const dynamic = 'force-dynamic';

function getCurrentMonthStart() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ timeframe?: string; date?: string }>;
}) {
  const query = await searchParams;

  // Use timeframe from URL search params if provided, otherwise default to current month
  const initialTimeframe = query.timeframe || 'month';
  const initialDate = initialTimeframe === 'all' ? null : (query.date || getCurrentMonthStart());

  return (
    <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-foreground">
      <Dashboard
        initialTimeframe={initialTimeframe}
        initialDate={initialDate}
      />
    </main>
  );
}
