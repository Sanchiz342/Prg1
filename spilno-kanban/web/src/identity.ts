// Who "I" am on boards: a display name and an ink colour, remembered per browser. No accounts.
export const INKS = ['#2F5BEA', '#E5484D', '#12A594', '#D9822B', '#8E4EC6', '#0091FF', '#C2298A', '#5C7C2F'];

export interface Identity {
  name: string;
  color: string;
}

const KEY = 'spilno-identity';

function read(): Identity | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? 'null');
    if (v && typeof v.name === 'string' && typeof v.color === 'string') return v;
  } catch {
    /* storage unavailable or corrupt: fall through */
  }
  return null;
}

export function getIdentity(): Identity {
  const existing = read();
  if (existing) return existing;
  const id: Identity = {
    name: `Гість ${Math.floor(100 + Math.random() * 900)}`,
    color: INKS[Math.floor(Math.random() * INKS.length)]!,
  };
  saveIdentity(id);
  return id;
}

export function saveIdentity(id: Identity): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ name: id.name.trim().slice(0, 40) || 'Гість', color: id.color }));
  } catch {
    /* ignore: identity just won't persist */
  }
}

export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join('') || '?';
