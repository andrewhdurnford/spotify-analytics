'use client';

import Image from 'next/image';
import Link from 'next/link';

interface MediaCardProps {
  name: string;
  subtitle: string;
  imageSrc: string | null;
  href?: string;
}

export default function MediaCard({ name, subtitle, imageSrc, href }: MediaCardProps) {
  const content = (
    <div className="flex flex-col items-start">
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

      <div className="text-muted text-xs mt-2 whitespace-normal wrap-break-word">
        {subtitle}
      </div>
    </div>
  );

  if (href) {
    return (
      <Link
        href={href}
        className="rounded-lg font-lexend font-light w-32 sm:w-40 shrink-0 hover:opacity-80 transition-opacity"
      >
        {content}
      </Link>
    );
  }

  return (
    <div className="rounded-lg font-lexend font-light w-32 sm:w-40 shrink-0">
      {content}
    </div>
  );
}
