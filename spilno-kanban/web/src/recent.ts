// Boards opened in this browser, newest first (the server has no accounts, so this is the "my boards" list).
export interface RecentBoard {
  id: string;
  title: string;
  at: number;
}

const KEY = 'spilno-recent';
const MAX = 12;

export function recentBoards(): RecentBoard[] {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    return Array.isArray(list) ? list.filter((b) => typeof b?.id === 'string').slice(0, MAX) : [];
  } catch {
    return [];
  }
}

export function rememberBoard(id: string, title: string): void {
  try {
    const list = [{ id, title, at: Date.now() }, ...recentBoards().filter((b) => b.id !== id)].slice(0, MAX);
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* ignore */
  }
}

export function forgetBoard(id: string): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(recentBoards().filter((b) => b.id !== id)));
  } catch {
    /* ignore */
  }
}
