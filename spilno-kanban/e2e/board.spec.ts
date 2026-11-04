import { expect, test, type Browser, type Page } from '@playwright/test';

/** A separate browser profile (own IndexedDB, own identity) = a separate person. */
async function person(browser: Browser, name: string, color: string): Promise<Page> {
  const ctx = await browser.newContext();
  await ctx.addInitScript(
    ([n, c]) => localStorage.setItem('spilno-identity', JSON.stringify({ name: n, color: c })),
    [name, color],
  );
  const page = await ctx.newPage();
  page.on('pageerror', (e) => {
    throw e;
  });
  return page;
}

async function newBoard(page: Page): Promise<string> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Створити дошку' }).click();
  await expect(page.locator('.column')).toHaveCount(3);
  await expect(page.getByTestId('connection')).toHaveText('Збережено');
  return page.url();
}

const column = (page: Page, title: string) => page.getByRole('region', { name: `Колонка «${title}»` });

async function addCard(page: Page, col: string, title: string) {
  await column(page, col).getByRole('button', { name: '+ Картка' }).click();
  await page.keyboard.type(title);
  await page.keyboard.press('Enter');
  await page.keyboard.press('Escape');
}

const cardTitles = (page: Page, col: string) => column(page, col).locator('.card-title');

/**
 * Moves the focused card with the keyboard: Space, arrows, Space. dnd-kit attaches its key handlers on the next
 * tick after pick-up (so the activating key is not handled twice), hence the short human-like pauses.
 */
async function keyboardMove(page: Page, cardTitle: string, keys: string[]) {
  await page.getByTestId('card').filter({ hasText: cardTitle }).focus();
  await page.keyboard.press('Space');
  for (const k of keys) {
    await page.waitForTimeout(120);
    await page.keyboard.press(k);
  }
  await page.waitForTimeout(120);
  await page.keyboard.press('Space');
}

test('two people see each other’s edits and presence live', async ({ browser }) => {
  const olya = await person(browser, 'Оля Коваль', '#2F5BEA');
  const taras = await person(browser, 'Тарас Бойко', '#E5484D');
  const url = await newBoard(olya);
  await taras.goto(url);
  await expect(taras.locator('.column')).toHaveCount(3);

  await addCard(olya, 'Зробити', 'Зібрати вимоги');
  await expect(cardTitles(taras, 'Зробити')).toHaveText(['Зібрати вимоги']);

  // Presence: each sees the other in the top bar.
  await expect(olya.locator('.presence')).toHaveAttribute('aria-label', 'Зараз на дошці: Тарас Бойко');
  await expect(taras.locator('.presence')).toHaveAttribute('aria-label', 'Зараз на дошці: Оля Коваль');

  // Opening a card shows my ink tab on it for the other person.
  await taras.getByText('Зібрати вимоги').click();
  await expect(olya.locator('.ink-tab')).toHaveText('ТБ');
  await taras.keyboard.press('Escape');
  await expect(olya.locator('.ink-tab')).toHaveCount(0);

  // Renaming a column and the board travels too.
  await taras.getByRole('button', { name: 'Готово', exact: true }).click();
  await taras.keyboard.press('Control+A');
  await taras.keyboard.type('Зроблено');
  await taras.keyboard.press('Enter');
  await expect(column(olya, 'Зроблено')).toBeVisible();
});

test('both people edit offline, and everything merges when they reconnect', async ({ browser }) => {
  const olya = await person(browser, 'Оля', '#2F5BEA');
  const taras = await person(browser, 'Тарас', '#E5484D');
  const url = await newBoard(olya);
  await addCard(olya, 'Зробити', 'Спільна картка');
  await taras.goto(url);
  await expect(cardTitles(taras, 'Зробити')).toHaveText(['Спільна картка']);

  await olya.context().setOffline(true);
  await taras.context().setOffline(true);
  await expect(olya.getByTestId('connection')).toHaveText('Офлайн');
  await expect(taras.getByTestId('connection')).toHaveText('Офлайн');

  await addCard(olya, 'Зробити', 'Олина офлайн-картка');
  await addCard(taras, 'В роботі', 'Тарасова офлайн-картка');
  // Concurrent edits of the same card while disconnected: Olya renames it, Taras moves it.
  await olya.getByText('Спільна картка').click();
  await olya.getByLabel('Назва', { exact: true }).fill('Спільна картка (перейменована)');
  await olya.keyboard.press('Enter');
  await olya.keyboard.press('Escape');
  await keyboardMove(taras, 'Спільна картка', ['ArrowRight']);
  await expect(cardTitles(taras, 'В роботі')).toContainText(['Спільна картка']);

  await olya.context().setOffline(false);
  await taras.context().setOffline(false);
  for (const p of [olya, taras]) {
    await expect(p.getByTestId('connection')).toHaveText('Збережено', { timeout: 15_000 });
    await expect(cardTitles(p, 'Зробити')).toHaveText(['Олина офлайн-картка']);
    // Two cards were added to this column concurrently: their relative order comes from random keys, so
    // check the set, not the order. The renamed card must also have been moved (both edits kept).
    await expect
      .poll(async () => (await cardTitles(p, 'В роботі').allTextContents()).sort(), { timeout: 10_000 })
      .toEqual(['Спільна картка (перейменована)', 'Тарасова офлайн-картка']);
  }
});

test('edits made offline survive closing the tab', async ({ browser }) => {
  const olya = await person(browser, 'Оля', '#2F5BEA');
  const url = await newBoard(olya);
  await olya.context().setOffline(true);
  await expect(olya.getByTestId('connection')).toHaveText('Офлайн');
  await addCard(olya, 'Зробити', 'Записано без мережі');
  await expect(cardTitles(olya, 'Зробити')).toHaveText(['Записано без мережі']);
  await olya.close(); // tab closed before the edit ever reached the server

  const ctx = olya.context();
  await ctx.setOffline(false);
  const again = await ctx.newPage();
  await again.goto(url); // same browser profile: the IndexedDB copy still has the card and syncs it now

  const taras = await person(browser, 'Тарас', '#E5484D');
  await taras.goto(url);
  await expect(cardTitles(taras, 'Зробити')).toHaveText(['Записано без мережі']);
});

test('undo reverts only my own changes', async ({ browser }) => {
  const olya = await person(browser, 'Оля', '#2F5BEA');
  const taras = await person(browser, 'Тарас', '#E5484D');
  const url = await newBoard(olya);
  await taras.goto(url);
  await addCard(olya, 'Зробити', 'Олина');
  await addCard(taras, 'Зробити', 'Тарасова');
  await expect(cardTitles(olya, 'Зробити')).toHaveCount(2);

  await olya.locator('body').click({ position: { x: 900, y: 600 } });
  await olya.keyboard.press('Control+Z');
  await expect(cardTitles(olya, 'Зробити')).toHaveText(['Тарасова']);
  await expect(cardTitles(taras, 'Зробити')).toHaveText(['Тарасова']);
  await olya.getByRole('button', { name: 'Повернути' }).click();
  await expect(cardTitles(taras, 'Зробити')).toHaveCount(2);
});

test('cards move with the mouse and with the keyboard', async ({ browser }) => {
  const olya = await person(browser, 'Оля', '#2F5BEA');
  await newBoard(olya);
  for (const t of ['Перша', 'Друга', 'Третя']) await addCard(olya, 'Зробити', t);
  await expect(cardTitles(olya, 'Зробити')).toHaveText(['Перша', 'Друга', 'Третя']);

  // Mouse: drag "Перша" into the empty "Готово" column.
  const card = olya.getByTestId('card').filter({ hasText: 'Перша' });
  const target = column(olya, 'Готово').locator('.cards');
  const from = (await card.boundingBox())!;
  const to = (await target.boundingBox())!;
  await olya.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await olya.mouse.down();
  await olya.mouse.move(from.x + 40, from.y + 10, { steps: 5 });
  await olya.mouse.move(to.x + to.width / 2, to.y + 20, { steps: 15 });
  await olya.mouse.up();
  await expect(cardTitles(olya, 'Готово')).toHaveText(['Перша']);
  await expect(olya.locator('.drawer')).toHaveCount(0); // the drop did not open the card

  // Keyboard: pick up "Третя", move it up one slot, drop; then move it across to the next column.
  await keyboardMove(olya, 'Третя', ['ArrowUp']);
  await expect(cardTitles(olya, 'Зробити')).toHaveText(['Третя', 'Друга']);
  await keyboardMove(olya, 'Третя', ['ArrowRight']);
  await expect(cardTitles(olya, 'В роботі')).toHaveText(['Третя']);
  await keyboardMove(olya, 'Третя', ['ArrowRight']);
  await expect(cardTitles(olya, 'Готово')).toContainText(['Третя']);

  // Enter opens the focused card.
  await olya.getByTestId('card').filter({ hasText: 'Друга' }).focus();
  await olya.keyboard.press('Enter');
  await expect(olya.getByRole('dialog', { name: 'Картка «Друга»' })).toBeVisible();
});

test('two people typing in the same description at once both keep their text', async ({ browser }) => {
  const olya = await person(browser, 'Оля', '#2F5BEA');
  const taras = await person(browser, 'Тарас', '#E5484D');
  const url = await newBoard(olya);
  await taras.goto(url);
  await addCard(olya, 'Зробити', 'Опис разом');
  for (const p of [olya, taras]) await p.getByText('Опис разом').click();
  await olya.locator('.description').fill('середина');
  await expect(taras.locator('.description')).toHaveValue('середина');

  // Olya types at the start, Taras at the end, interleaved keystroke by keystroke.
  await olya.locator('.description').press('Home');
  await taras.locator('.description').press('End');
  for (const [a, b] of [['П', '!'], ['о', '!'], ['ч', '!']]) {
    await olya.keyboard.type(a!);
    await taras.keyboard.type(b!);
  }
  for (const p of [olya, taras]) await expect(p.locator('.description')).toHaveValue('Почсередина!!!');
});

test('unknown pages and bad board links are handled', async ({ page }) => {
  await page.goto('/b/x');
  await expect(page.getByRole('heading', { name: 'Такої сторінки немає' })).toBeVisible();
  await page.getByRole('button', { name: 'На головну' }).click();
  await expect(page.getByRole('button', { name: 'Створити дошку' })).toBeVisible();
});
