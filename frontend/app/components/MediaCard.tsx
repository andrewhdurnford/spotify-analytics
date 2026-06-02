'use client';

import type { ReactNode } from 'react';
import Image from 'next/image';
import Link from 'next/link';

interface MediaCardProps {
  name: string;
  /** Plain string subtitle or ReactNode (e.g. text mixed with Links). */
  subtitle: ReactNode;
  imageSrc: string | null;
  /** Optional link target for the image + title. Subtitle is always rendered
   *  outside any wrapping link so it can contain its own Links. */
  href?: string;
}

export default function MediaCard({ name, subtitle, imageSrc, href }: MediaCardProps) {
  const primary = (
    <>
      {imageSrc && (
        <Image
          src={imageSrc}
          alt={name}
          width={160}
          height={160}
          className="w-32 h-32 sm:w-40 sm:h-40 object-cover rounded-md mb-3"
        />
      )}

      <div className="w-full">
        <div className="font-normal text-sm whitespace-normal wrap-break-word">
          {name}
        </div>
      </div>
    </>
  );

  return (
    <div className="rounded-lg font-lexend font-light w-32 sm:w-40 shrink-0 snap-start">
      {href ? (
        <Link
          href={href}
          className="flex flex-col items-start hover:opacity-80 transition-opacity"
        >
          {primary}
        </Link>
      ) : (
        <div className="flex flex-col items-start">{primary}</div>
      )}

      <div className="text-muted text-xs mt-2 whitespace-normal wrap-break-word">
        {subtitle}
      </div>
    </div>
  );
}
