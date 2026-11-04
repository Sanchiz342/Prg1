// Keyboard moves across columns. dnd-kit's sortableKeyboardCoordinates only knows "the nearest sortable item in
// that direction", which in a multi-column board reorders within the column instead of crossing to the next
// one. Here Left/Right jump to the neighbouring column's drop zone; Up/Down keep the stock behaviour.
import { KeyboardCode, type KeyboardCoordinateGetter } from '@dnd-kit/core';
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable';

export const boardKeyboardCoordinates: KeyboardCoordinateGetter = (event, args) => {
  if (event.code !== KeyboardCode.Left && event.code !== KeyboardCode.Right) {
    return sortableKeyboardCoordinates(event, args);
  }
  event.preventDefault();
  const { active, collisionRect, droppableContainers, droppableRects } = args.context;
  if (!active || !collisionRect) return undefined;

  const columns = droppableContainers
    .getEnabled()
    .filter((c) => String(c.id).startsWith('drop:'))
    .map((c) => ({ id: String(c.id), rect: droppableRects.get(c.id) }))
    .filter((c): c is { id: string; rect: NonNullable<typeof c.rect> } => !!c.rect)
    .sort((a, b) => a.rect.left - b.rect.left);

  const containerId = (active.data.current as { sortable?: { containerId?: string } } | undefined)?.sortable?.containerId;
  let index = columns.findIndex((c) => c.id === `drop:${containerId}`);
  if (index === -1) {
    const x = collisionRect.left + collisionRect.width / 2;
    index = columns.findIndex((c) => x >= c.rect.left && x <= c.rect.right);
  }
  const target = columns[index + (event.code === KeyboardCode.Right ? 1 : -1)];
  if (!target) return undefined;
  // Land at the top of the target column; collision detection then picks its first card (or the empty zone).
  return { x: target.rect.left + (target.rect.width - collisionRect.width) / 2, y: target.rect.top + 2 };
};
