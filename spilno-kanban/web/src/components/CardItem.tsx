import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import type { CardView } from '../board/model';
import { initials } from '../identity';
import type { Peer } from '../session';

export const LABEL_NAMES: Record<string, string> = {
  red: 'Терміново',
  orange: 'Важливо',
  yellow: 'Чекає',
  green: 'Готово до перевірки',
  blue: 'Ідея',
  purple: 'Дизайн',
};

export function SortableCard({ card, peers, onOpen }: { card: CardView; peers: Peer[]; onOpen: (via: 'pointer' | 'keyboard') => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: card.id });
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={`card-slot${isDragging ? ' placeholder' : ''}`}
      {...attributes}
      {...listeners}
      aria-roledescription="картка, яку можна перетягнути"
      aria-label={card.title}
      onKeyDown={(e) => {
        listeners?.onKeyDown?.(e);
        if (e.key === 'Enter' && !e.defaultPrevented) onOpen('keyboard');
      }}
      onClick={() => onOpen('pointer')}
      data-testid="card"
    >
      <CardFace card={card} peers={peers} />
    </li>
  );
}

/** The visual card (also used in the drag overlay). */
export function CardFace({ card, peers, lifted }: { card: CardView; peers: Peer[]; lifted?: boolean }) {
  const hasDescription = (card.description?.length ?? 0) > 0;
  return (
    <article className={`card${lifted ? ' lifted' : ''}${peers.length ? ' watched' : ''}`} style={peers[0] ? ({ '--ink': peers[0].color } as React.CSSProperties) : undefined}>
      {peers.length > 0 && (
        <div className="ink-tabs" aria-label={`Зараз з карткою: ${peers.map((p) => p.name).join(', ')}`}>
          {peers.slice(0, 3).map((p) => (
            <span key={p.clientId} className="ink-tab" style={{ '--ink': p.color } as React.CSSProperties} title={`${p.name} ${p.dragging === card.id ? 'переносить' : 'редагує'}`}>
              {initials(p.name)}
            </span>
          ))}
        </div>
      )}
      {card.labels.length > 0 && (
        <div className="labels">
          {card.labels.map((l) => (
            <span key={l} className={`label label-${l}`} title={LABEL_NAMES[l]} />
          ))}
        </div>
      )}
      <p className="card-title">{card.title}</p>
      {(card.assignee || hasDescription) && (
        <footer className="card-meta">
          {hasDescription && (
            <span className="has-desc" title="Є опис" aria-label="Є опис">
              ¶
            </span>
          )}
          {card.assignee && <span className="assignee">{card.assignee}</span>}
        </footer>
      )}
    </article>
  );
}
