import { createContext, useContext } from 'react';
import type { Lang, TFunction } from '../i18n';

/**
 * Ambient app-wide state shared across views, so it no longer has to be
 * threaded through every component as props: i18n (lang + translator), the
 * global error banner, the global "busy" marker, and the header search query.
 */
export type AppContextValue = {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: TFunction;
  canvasBaseUrl: string;
  error: string | null;
  setError: (value: string | null) => void;
  busy: string | null;
  setBusy: (value: string | null) => void;
  query: string;
  setQuery: (value: string) => void;
};

export const AppContext = createContext<AppContextValue | null>(null);

export function useAppContext(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) {
    throw new Error('useAppContext must be used within an AppContext provider');
  }
  return value;
}
