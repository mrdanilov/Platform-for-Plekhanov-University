// Проверка логики паузы на кнопке Excel без браузера.
//
// Запускается из schedule/test_cooldown_js.py и пропускается, если node не
// установлен. Скрипт вырезается из шаблона index.html и выполняется против
// подменённых document/localStorage/Date: таймер реально тикает, а не просто
// ищется в исходнике. Время двигается вручную, setInterval заменён на очередь.
//
// Проверять приходится именно исполнением: питоновские тесты видят только
// наличие строк в HTML и не поймают, что отсчёт не тикает или что пауза
// слетает при перезагрузке страницы.

const fs = require('fs');
const path = require('path');

const templatePath = path.join(
  __dirname, '..', 'templates', 'schedule', 'index.html');
const template = fs.readFileSync(templatePath, 'utf-8');

const blocks = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const code = blocks[blocks.length - 1];

let fails = 0;
function check(name, cond, extra) {
  if (cond) { console.log('  ok   ' + name); }
  else { fails++; console.log('  FAIL ' + name + (extra ? '  -> ' + extra : '')); }
}

// --- подменяемое окружение -----------------------------------------------
let now = 1_000_000;
const store = {};
let timers = [];

class El {
  constructor(ds = {}) {
    this.dataset = ds;
    this._text = '';
    this._attrs = {};
    this._cls = new Set();
    this._subs = {};
    this.classList = {
      add: c => this._cls.add(c),
      remove: c => this._cls.delete(c),
      contains: c => this._cls.has(c),
    };
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = v; }
  setAttribute(k, v) { this._attrs[k] = v; }
  removeAttribute(k) { delete this._attrs[k]; }
  addEventListener(t, f) { (this._subs[t] ||= []).push(f); }
  querySelector(sel) { return sel === '.dl-cool' ? this.cool : null; }
}

function build(opts = {}) {
  timers = [];
  const broken = !!opts.brokenStorage;
  const link = new El({
    cooldown: '30', coolTip: 'Повторная выгрузка через', secLabel: 'с',
  });
  link.cool = new El();
  const stub = () => new El();
  const elements = {
    'schedule-block': stub(), 'picker-backdrop': stub(), 'picker-list': stub(),
    'picker-search': stub(), 'picker-title': stub(), 'picker-close': stub(),
    'week-param': stub(),
  };
  elements['week-param'].textContent = '"2026-W41"';

  const clicks = [];
  const doc = {
    getElementById: id => elements[id] || null,
    querySelectorAll: () => [link],
    addEventListener: (t, f) => { if (t === 'click') clicks.push(f); },
  };

  const sandbox = {
    document: doc,
    localStorage: broken ? {
      getItem() { throw new Error('private mode'); },
      setItem() { throw new Error('private mode'); },
    } : {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    Date: { now: () => now },
    setInterval: fn => { timers.push(fn); return timers.length; },
    history: { replaceState() {} },
    URLSearchParams,
    console,
  };

  const names = Object.keys(sandbox);
  const run = new Function(...names, code);
  run(...names.map(n => sandbox[n]));

  // клик по кнопке: прогоняем через все делегированные обработчики,
  // чтобы рабочая проверка была именно на делегировании, а не на find
  let blocked = false;
  const click = () => {
    blocked = false;
    const ev = {
      target: { closest: s => (s === '[data-cooldown]' ? link : null) },
      preventDefault: () => { blocked = true; },
    };
    clicks.forEach(h => h(ev));
    return blocked;
  };
  const tick = () => timers.forEach(t => t());
  return { link, click, tick };
}

// --- проверки -----------------------------------------------------------
const t = build();

check('до клика кнопка не помечена паузой', !t.link.classList.contains('is-cooling'));
check('до клика счётчик пуст', t.link.cool.textContent === '', t.link.cool.textContent);

const first = t.click();
check('клик включил класс паузы', t.link.classList.contains('is-cooling'));
check('первый клик не заблокирован (выгрузка пошла)', first === false);
check('показаны 30 секунд', /30/.test(t.link.cool.textContent), t.link.cool.textContent);
check('подпись секунд подставлена', /[^0-9]\s*[^0-9]/.test(t.link.cool.textContent), t.link.cool.textContent);
check('aria-disabled выставлен', t.link._attrs['aria-disabled'] === 'true');
check('подсказка содержит остаток', /30/.test(t.link._attrs.title || ''), t.link._attrs.title);

now += 5000;
t.tick();
check('через 5 с осталось 25', /25/.test(t.link.cool.textContent), t.link.cool.textContent);
const second = t.click();
check('повторный клик заблокирован', second === true);
check('повторный клик не сбросил таймер', /25/.test(t.link.cool.textContent), t.link.cool.textContent);

now += 24_000;
t.tick();
check('на 29-й секунде ещё пауза', t.link.classList.contains('is-cooling'));
now += 1_500;
t.tick();
check('после 30 с пауза снята', !t.link.classList.contains('is-cooling'));
check('счётчик очищен', t.link.cool.textContent === '', t.link.cool.textContent);
check('aria-disabled убран', t.link._attrs['aria-disabled'] === undefined);

const third = t.click();
check('клик после паузы снова проходит', third === false);
check('пауза запущена заново', t.link.classList.contains('is-cooling'));

// перезагрузка страницы: скрипт выполняется заново, store общий
const fresh = build();
check('пауза пережила перезагрузку', fresh.link.classList.contains('is-cooling'),
      'localStorage должен сохранить остаток');
check('после перезагрузки клик заблокирован', fresh.click() === true);

check('в localStorage метка времени, а не счётчик',
      Number(store.schedule_export_cool_until) > now, store.schedule_export_cool_until);

// приватный режим: localStorage бросает исключение. Пауза обязана работать
// хотя бы внутри вкладки, иначе страница падает с ошибкой
const p = build({ brokenStorage: true });
const pFirst = p.click();
check('приватный режим: клик не бросает ошибку', pFirst === false);
check('приватный режим: пауза включилась', p.link.classList.contains('is-cooling'));
p.tick();
check('приватный режим: отсчёт тикает',
      /30/.test(p.link.cool.textContent), p.link.cool.textContent);
check('приватный режим: повторный клик заблокирован', p.click() === true);
now += 31_000;
p.tick();
check('приватный режим: пауза истекает', !p.link.classList.contains('is-cooling'));

console.log('');
if (fails) {
  console.log('  ПРОВАЛЕНО: ' + fails);
  process.exit(1);
}
console.log('  Все проверки паузы пройдены.');
