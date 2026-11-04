import { useState } from 'react';

import { newBoardId } from './board/ids';
import { navigate, useRoute } from './router';
import { forgetBoard, recentBoards, type RecentBoard } from './recent';
import { BoardPage } from './components/BoardPage';

export function App() {
  const route = useRoute();
  if (route.name === 'board') return <BoardPage key={route.id} id={route.id} isNew={route.isNew} />;
  if (route.name === 'notFound') return <NotFound />;
  return <Home />;
}

function Home() {
  const [recent, setRecent] = useState<RecentBoard[]>(recentBoards);
  const create = () => navigate(`/b/${newBoardId()}?new=1`);
  const relative = (at: number) => {
    const min = Math.round((Date.now() - at) / 60_000);
    if (min < 1) return 'щойно';
    if (min < 60) return `${min} хв тому`;
    const h = Math.round(min / 60);
    if (h < 24) return `${h} год тому`;
    return new Date(at).toLocaleDateString('uk-UA');
  };
  return (
    <main className="home">
      <header className="home-head">
        <span className="brand">spilno</span>
      </header>
      <section className="hero">
        <h1>
          Одна дошка.
          <br />
          Усі правлять <span className="ink-under">одночасно</span>.
        </h1>
        <p className="lead">
          Створіть дошку й надішліть посилання команді. Картки рухаються в усіх одразу, видно, хто що відкрив, а
          без інтернету робота продовжується — зміни зіллються, щойно мережа повернеться.
        </p>
        <button className="btn primary big" onClick={create}>
          Створити дошку
        </button>
        <p className="hint">Без реєстрації. Хто має посилання, той може редагувати.</p>
      </section>
      {recent.length > 0 && (
        <section className="recent" aria-labelledby="recent-title">
          <h2 id="recent-title">Нещодавні дошки</h2>
          <ul>
            {recent.map((b) => (
              <li key={b.id}>
                <a
                  href={`/b/${b.id}`}
                  onClick={(e) => {
                    e.preventDefault();
                    navigate(`/b/${b.id}`);
                  }}
                >
                  <span className="recent-title">{b.title || 'Без назви'}</span>
                  <span className="recent-at">{relative(b.at)}</span>
                </a>
                <button
                  className="icon-btn"
                  aria-label={`Прибрати «${b.title}» зі списку`}
                  title="Прибрати зі списку (дошка залишиться за посиланням)"
                  onClick={() => {
                    forgetBoard(b.id);
                    setRecent(recentBoards());
                  }}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}

function NotFound() {
  return (
    <main className="home">
      <section className="hero">
        <h1>Такої сторінки немає</h1>
        <p className="lead">Можливо, посилання на дошку скопіювалося не повністю.</p>
        <button className="btn primary" onClick={() => navigate('/')}>
          На головну
        </button>
      </section>
    </main>
  );
}
