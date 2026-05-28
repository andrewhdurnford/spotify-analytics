import React, { useState, useRef, useEffect, useCallback } from 'react'

interface Props<T> {
  options: T[]
  selected: T
  onSelect: (value: T) => void
  formatLabel?: (value: T) => string
  label?: string
}

export default function Dropdown<T extends string | number>({ 
  options, 
  selected, 
  onSelect,
  formatLabel,
  label
}: Props<T>) {
  const [isOpen, setIsOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const listRef = useRef<HTMLUListElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)

  const toggle = () => {
    setIsOpen(o => {
      const next = !o
      if (next) {
        // Set active index to the currently selected option
        const idx = options.indexOf(selected)
        setActiveIndex(idx >= 0 ? idx : 0)
      }
      return next
    })
  }

  const close = useCallback(() => {
    setIsOpen(false)
    setActiveIndex(-1)
    buttonRef.current?.focus()
  }, [])

  const selectOption = useCallback((value: T) => {
    onSelect(value)
    close()
  }, [onSelect, close])

  const getLabel = (value: T): string => {
    if (formatLabel) {
      return formatLabel(value)
    }
    return String(value)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        toggle()
      }
      return
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActiveIndex(i => (i + 1) % options.length)
        break
      case 'ArrowUp':
        e.preventDefault()
        setActiveIndex(i => (i - 1 + options.length) % options.length)
        break
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (activeIndex >= 0 && activeIndex < options.length) {
          selectOption(options[activeIndex])
        }
        break
      case 'Escape':
        e.preventDefault()
        close()
        break
      case 'Tab':
        close()
        break
      case 'Home':
        e.preventDefault()
        setActiveIndex(0)
        break
      case 'End':
        e.preventDefault()
        setActiveIndex(options.length - 1)
        break
    }
  }

  // Scroll active option into view
  useEffect(() => {
    if (isOpen && activeIndex >= 0 && listRef.current) {
      const activeEl = listRef.current.children[activeIndex] as HTMLElement | undefined
      activeEl?.scrollIntoView({ block: 'nearest' })
    }
  }, [isOpen, activeIndex])

  const listboxId = `dropdown-listbox-${label ?? 'default'}`

  return (
    <div className="relative inline-block w-40" onKeyDown={handleKeyDown}>
      <button
        ref={buttonRef}
        onClick={toggle}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-label={label}
        aria-controls={isOpen ? listboxId : undefined}
        className="font-['lexend'] font-light text-[#F4F4F9] hover:text-white hover:underline hover:cursor-pointer transition-transform duration-250 hover:scale-105 min-h-[44px] flex items-center"
      >
        {getLabel(selected)}
      </button>

      {isOpen && (
        <ul
          ref={listRef}
          role="listbox"
          id={listboxId}
          aria-label={label}
          tabIndex={-1}
          className="absolute right-0 mt-1 w-40 bg-[#1a1a1a] border border-[#333] rounded-lg shadow-lg z-20"
        >
          {options.map((value, idx) => (
            <li
              key={String(value)}
              id={`${listboxId}-option-${idx}`}
              role="option"
              aria-selected={value === selected}
              onClick={() => selectOption(value)}
              className={`px-3 py-2 min-h-[44px] flex items-center font-['lexend'] font-light text-[#F4F4F9] transition-transform duration-250 
                ${idx === activeIndex ? 'bg-[#2a2a2a]' : ''}
                ${
                value === selected 
                  ? 'font-normal' 
                  : 'hover:scale-105 hover:cursor-pointer hover:underline hover:text-white active:bg-[#2a2a2a]'
              }`}
            >
              {getLabel(value)}
            </li>
          ))}
        </ul>
      )}

      {isOpen && (
        <div
          className="fixed inset-0 z-10"
          onClick={close}
        />
      )}
    </div>
  )
}
