// Tiny history-API router: two routes do not justify a routing library.
import { useEffect, useState } from 'react';

export const BOARD_ID = /^[A-Za-z0-9_-]{10,64}$/;

export type Route = { name: 'home' } | { name: 'board'; id: string; isNew: boolean } | { name: 'notFound' };

export function parse(loc: Location): Route {
  if (loc.pathname === '/' || loc.pathname === '') return { name: 'home' };
  const m = /^\/b\/([^/]+)\/?$/.exec(loc.pathname);
  if (m && BOARD_ID.test(m[1]!)) {
    return { name: 'board', id: m[1]!, isNew: new URLSearchParams(loc.search).get('new') === '1' };
  }
  return { name: 'notFound' };
}

export function navigate(path: string, replace = false): void {
  if (replace) history.replaceState(null, '', path);
  else history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location));
  useEffect(() => {
    const onPop = () => setRoute(parse(location));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  return route;
}
