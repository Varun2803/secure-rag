import { Moon, Sun } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { PropsWithChildren } from 'react'
import { Button } from '@/components/ui/button'
import { ThemeContext, useTheme } from './theme-context'
import type { Theme } from './theme-context'

function getInitialTheme(): Theme {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

export function ThemeProvider({ children }: PropsWithChildren) {
  const [theme, setTheme] = useState<Theme>(getInitialTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.style.colorScheme = theme
  }, [theme])

  const value = useMemo(
    () => ({ theme, toggleTheme: () => setTheme((current) => current === 'light' ? 'dark' : 'light') }),
    [theme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme()
  const LabelIcon = theme === 'light' ? Moon : Sun

  return (
    <Button aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`} onClick={toggleTheme} size="icon" variant="ghost">
      <LabelIcon aria-hidden="true" className="size-4" />
    </Button>
  )
}
