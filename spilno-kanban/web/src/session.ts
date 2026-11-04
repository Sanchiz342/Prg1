// A live board session: the Yjs document plus everything attached to it (IndexedDB copy, WebSocket sync,
// presence, undo). React reads it through useSyncExternalStore; the snapshot is rebuilt at most once per frame.
import { useEffect, useState, useSyncExternalStore } from 'react';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import { IndexeddbPersistence } from 'y-indexeddb';

import { Board, createUndoManager, type BoardView } from './board/model';
import { getIdentity, isInkColor, type Identity } from './identity';
import { rememberBoard } from './recent';

export type Connection = 'connecting' | 'online' | 'offline';

export interface Peer {
  clientId: number;
  name: string;
  color: string;
  editing: string | null;
  dragging: string | null;
}

export interface Snapshot {
  view: BoardView;
  peers: Peer[];
  connection: Connection;
  /** The server has confirmed it has everything we have (and we have everything it has). */
  synced: boolean;
  /** The local IndexedDB copy has been read. */
  loaded: boolean;
  canUndo: boolean;
  canRedo: boolean;
}

interface Presence {
  user: Identity;
  editing: string | null;
  dragging: string | null;
}

export const wsUrl = () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;

export class BoardSession {
  readonly doc = new Y.Doc();
  readonly board: Board;
  readonly undo: Y.UndoManager;
  private provider: WebsocketProvider;
  private idb: IndexeddbPersistence;
  private listeners = new Set<() => void>();
  private snapshot: Snapshot;
  private frame = 0;
  private loaded = false;
  private connection: Connection = 'connecting';
  private destroyed = false;
  private rememberedTitle: string | null = null;

  constructor(
    readonly id: string,
    isNew: boolean,
  ) {
    this.board = new Board(this.doc);
    // Attach the local IndexedDB copy first, so even the very first edit (the initialisation below) is stored.
    this.idb = new IndexeddbPersistence(`spilno-${id}`, this.doc);
    this.idb.whenSynced.then(() => {
      this.loaded = true;
      this.schedule();
    });
    // A brand-new board id is random, so nobody else can have content for it yet: initialise immediately,
    // even offline. Joining an existing board never initialises (that could overwrite its title).
    if (isNew) this.board.init('Нова дошка');
    this.undo = createUndoManager(this.board);

    this.provider = new WebsocketProvider(wsUrl(), id, this.doc, { maxBackoffTime: 5000 });
    this.provider.on('status', ({ status }: { status: string }) => {
      this.connection = !navigator.onLine ? 'offline' : status === 'connected' ? 'online' : 'connecting';
      this.schedule();
    });
    this.provider.on('sync', () => this.schedule());
    this.provider.awareness.setLocalState({ user: getIdentity(), editing: null, dragging: null } satisfies Presence);
    this.provider.awareness.on('change', () => this.schedule());

    this.doc.on('update', () => this.schedule());
    this.undo.on('stack-item-added', () => this.schedule());
    this.undo.on('stack-item-popped', () => this.schedule());
    window.addEventListener('online', this.onNetwork);
    window.addEventListener('offline', this.onNetwork);

    this.snapshot = this.build();
  }

  private onNetwork = () => {
    // Derive from the socket's real state: coming back online does not always reconnect (the socket may have
    // survived), so assuming "connecting" here could get stuck with no further status event to correct it.
    this.connection = !navigator.onLine ? 'offline' : this.provider.wsconnected ? 'online' : 'connecting';
    this.schedule();
  };

  private build(): Snapshot {
    const me = this.doc.clientID;
    const peers: Peer[] = [];
    this.provider.awareness.getStates().forEach((state, clientId) => {
      const p = state as Partial<Presence>;
      if (clientId === me || !p.user || typeof p.user.name !== 'string') return;
      peers.push({
        clientId,
        name: p.user.name.slice(0, 40),
        color: isInkColor(p.user.color) ? p.user.color : '#7d88a1',
        editing: typeof p.editing === 'string' ? p.editing : null,
        dragging: typeof p.dragging === 'string' ? p.dragging : null,
      });
    });
    peers.sort((a, b) => a.name.localeCompare(b.name) || a.clientId - b.clientId);
    return {
      view: this.board.view(),
      peers,
      connection: this.connection,
      synced: this.provider.synced,
      loaded: this.loaded,
      canUndo: this.undo.canUndo(),
      canRedo: this.undo.canRedo(),
    };
  }

  /** Coalesces bursts of updates (typing, a peer dragging) into one React render per frame. */
  private schedule = () => {
    if (this.frame || this.destroyed) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.snapshot = this.build();
      if (this.snapshot.view.title !== this.rememberedTitle && (this.loaded || this.provider.synced)) {
        this.rememberedTitle = this.snapshot.view.title;
        rememberBoard(this.id, this.rememberedTitle);
      }
      for (const l of this.listeners) l();
    });
  };

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.snapshot;

  setPresence(patch: Partial<Omit<Presence, 'user'>> & { user?: Identity }): void {
    const current = (this.provider.awareness.getLocalState() ?? {}) as Presence;
    this.provider.awareness.setLocalState({ ...current, ...patch });
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.frame);
    window.removeEventListener('online', this.onNetwork);
    window.removeEventListener('offline', this.onNetwork);
    this.undo.destroy();
    this.provider.destroy();
    void this.idb.destroy();
    this.doc.destroy();
  }
}

/** Opens a session for `id` and keeps it for the component's lifetime (StrictMode-safe). */
export function useBoardSession(id: string, isNew: boolean): BoardSession | null {
  const [session, setSession] = useState<BoardSession | null>(null);
  useEffect(() => {
    const s = new BoardSession(id, isNew);
    setSession(s);
    return () => {
      s.destroy();
      setSession(null);
    };
  }, [id, isNew]);
  return session;
}

const EMPTY = () => () => {};
export function useSnapshot(session: BoardSession | null): Snapshot | null {
  return useSyncExternalStore(session?.subscribe ?? EMPTY, () => session?.getSnapshot() ?? null);
}
