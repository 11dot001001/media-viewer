'use strict';
// ============================================================ страница «Статистика»
// Всё считается в браузере по списку снимков (S.items) с учётом фильтров слева.
// Графики — простой SVG: год к году по месяцам, по годам, каждый день года, время суток, дни недели, что и чем снято.

const ST = {
  year: prefs.get('stYear', null),  // выбранный год (синий)
  cmp: prefs.get('stCmp', null),  // год для сравнения (оранжевый), 0 — без сравнения
  mode: prefs.get('stMode', 'month'),  // month — по месяцам, cum — нарастающим итогом с начала года
  data: null,
};
const WEEKDAYS_FULL = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'];
const WEEKDAYS_ON = ['по понедельникам', 'по вторникам', 'по средам', 'по четвергам', 'по пятницам', 'по субботам', 'по воскресеньям'];
const fmtN = n => Math.round(n).toLocaleString('ru-RU');
const fmtK = n => (n >= 10000 ? Math.round(n / 1000) + ' тыс.' : n >= 1000 ? (n / 1000).toFixed(1).replace('.', ',').replace(',0', '') + ' тыс.' : String(n));
// plural с разделителем разрядов: «14 693 снимка», а не «14693 снимка»
const pluralN = (n, a, b, c) => plural(Math.round(n), a, b, c).replace(/^\d+/, fmtN(n));
const shots = n => pluralN(n, 'снимок', 'снимка', 'снимков');
const keyOf = d => `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;

/** Все расчёты за один проход по снимкам. */
function statsCompute() {
  const items = S.items.filter(it => it.t != null && passFilters(it));
  const byDay = new Map(), years = new Map();
  const hours = new Array(24).fill(0), wdays = new Array(7).fill(0);
  const cams = new Map(), ai = new Map();
  let photos = 0, videos = 0, dur = 0, geo = 0;
  for (const it of items) {
    const d = new Date(it.t * 1000);
    const y = d.getUTCFullYear(), m = d.getUTCMonth(), k = keyOf(d);
    byDay.set(k, (byDay.get(k) || 0) + 1);
    const Y = years.get(y) || years.set(y, { months: new Array(12).fill(0), photos: 0, videos: 0 }).get(y);
    Y.months[m]++;
    if (it.k === 'v') { Y.videos++; videos++; dur += it.dur || 0; } else { Y.photos++; photos++; }
    hours[d.getUTCHours()]++;
    wdays[(d.getUTCDay() + 6) % 7]++;
    if (it.cam) cams.set(it.cam, (cams.get(it.cam) || 0) + 1);
    if (it.ai) ai.set(it.ai, (ai.get(it.ai) || 0) + 1);
    if (it.lat != null) geo++;
  }
  const days = [...byDay.keys()].sort();
  // самая длинная серия дней подряд и самый долгий перерыв
  let streak = { n: 0 }, gap = { n: 0 }, run = 0, runFrom = null, prev = null;
  for (const k of days) {
    const t = Date.parse(k + 'T00:00:00Z');
    if (prev != null && t - prev === 864e5) run++;
    else {
      if (prev != null && (t - prev) / 864e5 - 1 > gap.n) gap = { n: (t - prev) / 864e5 - 1, from: keyOf(new Date(prev + 864e5)), to: keyOf(new Date(t - 864e5)) };
      run = 1; runFrom = k;
    }
    if (run > streak.n) streak = { n: run, from: runFrom, to: k };
    prev = t;
  }
  let best = null;
  for (const [k, n] of byDay) if (!best || n > best.n) best = { k, n };
  return { items, byDay, days, years, hours, wdays, cams, ai, photos, videos, dur, geo, streak, gap, best };
}

// ------------------------------------------------------------ отрисовка страницы

function renderStats() {
  const el = $('#view-stats');
  const D = ST.data = statsCompute();
  const ys = [...D.years.keys()].sort((a, b) => a - b);
  if (!D.items.length) {
    el.innerHTML = '<div class="list-head"></div>' + emptyHTML('Нет снимков с датой', S.items.length ? 'Измените условия в панели «Фильтры и вид».' : '');
    return;
  }
  if (!D.years.has(ST.year)) ST.year = ys[ys.length - 1];
  if (ST.cmp !== 0 && (!D.years.has(ST.cmp) || ST.cmp === ST.year)) ST.cmp = ys.filter(y => y < ST.year).pop() ?? ys.find(y => y !== ST.year) ?? 0;
  const filtered = D.items.length !== S.items.filter(it => it.t != null).length;
  const span = ys.length > 1 ? `${ys[0]}–${ys[ys.length - 1]}` : String(ys[0]);

  const tile = (label, value, sub, act = '') => `<div class="st-tile${act ? ' click' : ''}" ${act}><div class="st-l">${label}</div><div class="st-v">${value}</div><div class="st-s">${sub}</div></div>`;
  const dayLink = k => `data-act="st-day" data-day="${k}" title="Показать этот день в ленте"`;
  const tiles = [
    tile('Снято', fmtN(D.items.length), `${fmtN(D.photos)} фото · ${fmtN(D.videos)} видео`),
    tile('Самый активный день', fmtN(D.best.n), fmtDay(D.best.k), dayLink(D.best.k)),
    D.streak.n > 1 ? tile('Дней подряд со съёмкой', fmtN(D.streak.n), rangeText(D.streak.from, D.streak.to), `data-act="st-range" data-from="${D.streak.from}" data-to="${D.streak.to}" title="Показать эти дни в ленте"`) : '',
    tile('В среднем за день съёмки', fmtN(D.items.length / D.days.length), `${pluralN(D.days.length, 'день', 'дня', 'дней')} со снимками`),
    D.videos ? tile('Видео', fmtDur(D.dur) ? durText(D.dur) : fmtN(D.videos), pluralN(D.videos, 'ролик', 'ролика', 'роликов')) : '',
  ].join('');

  const yearChips = ys.slice().reverse().map(y => `<button class="chip${y === ST.year ? ' active' : ''}" data-act="st-year" data-y="${y}">${y}</button>`).join('');
  const cmpChips = ys.length > 1 ? `<span class="st-ctl-l">сравнить с</span>` + ys.slice().reverse().filter(y => y !== ST.year)
    .map(y => `<button class="chip${y === ST.cmp ? ' active cmp' : ''}" data-act="st-cmp" data-y="${y}">${y}</button>`).join('') +
    `<button class="chip${!ST.cmp ? ' active' : ''}" data-act="st-cmp" data-y="0">ни с чем</button>` : '';

  el.innerHTML = `<div class="list-head"><span class="sub">${shots(D.items.length)} · ${span}</span>
      ${filtered ? '<span class="st-note">с учётом фильтров слева · <button class="link" data-act="st-reset">сбросить</button></span>' : ''}</div>
    <div class="st-tiles">${tiles}</div>
    <section class="card st-card">
      <div class="st-head"><h3>Год к году</h3>
        <div class="seg st-mode"><button data-act="st-mode" data-m="month" class="${ST.mode === 'month' ? 'active' : ''}">По месяцам</button><button data-act="st-mode" data-m="cum" class="${ST.mode === 'cum' ? 'active' : ''}">С начала года</button></div></div>
      <div class="st-ctl"><div class="chips">${yearChips}</div>${cmpChips ? `<div class="chips">${cmpChips}</div>` : ''}</div>
      <p class="st-insight">${yearInsight(D)}</p>
      <div class="st-chart" id="stYoY"></div>
      <div class="st-legend" id="stYoYLegend"></div>
      <details class="st-table"><summary>Таблица по месяцам</summary>${monthTable(D, ys)}</details>
    </section>
    <section class="card st-card"><div class="st-head"><h3>Каждый день ${ST.year} года</h3><span class="st-sub">клик по дню — открыть его в ленте</span></div>
      <div class="st-chart" id="stHeat"></div>
      <div class="st-legend heat"><span>меньше</span>${[0, 1, 2, 3, 4].map(l => `<i class="hl${l}"></i>`).join('')}<span>больше</span></div>
    </section>
    <div class="st-row">
      <section class="card st-card"><div class="st-head"><h3>По годам</h3></div>
        <div class="st-chart" id="stYears"></div>
        <div class="st-legend"><span class="key bar s1"></span>Фото<span class="key bar s2"></span>Видео</div></section>
      <section class="card st-card"><div class="st-head"><h3>Когда снимаете</h3></div>
        <p class="st-insight">${timeInsight(D)}</p>
        <div class="st-chart" id="stHours"></div>
        <div class="st-chart" id="stWeek"></div></section>
    </div>
    <div class="st-row">
      ${D.ai.size ? `<section class="card st-card"><div class="st-head"><h3>Что на снимках</h3><span class="st-sub">по анализу AI</span></div><div id="stAi"></div></section>` : ''}
      ${D.cams.size ? `<section class="card st-card"><div class="st-head"><h3>Чем снимали</h3></div><div id="stCams"></div></section>` : ''}
    </div>
    ${funFacts(D)}`;
  drawStats();
}

function durText(s) {
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} ч ${m} мин` : `${m} мин`;
}

/** Рисуем графики под реальную ширину карточек (после того как разметка встала на место). */
function drawStats() {
  const D = ST.data;
  if (!D || S.view !== 'stats') return;
  drawYoY(D);
  drawHeat(D);
  drawYears(D);
  drawColumns($('#stHours'), D.hours, i => (i % 6 === 0 ? `${i}:00` : ''), i => `${i}:00–${i + 1}:00`, 120);
  drawColumns($('#stWeek'), D.wdays, i => WEEKDAYS[i], i => WEEKDAYS_FULL[i], 110);
  if ($('#stAi')) drawHBars($('#stAi'), [...D.ai.entries()].map(([k, n]) => { const l = aiLabels().find(x => x.key === k); return [l ? `${l.emoji || ''} ${l.label}`.trim() : k, n]; }));
  if ($('#stCams')) drawHBars($('#stCams'), [...D.cams.entries()]);
}

// ------------------------------------------------------------ тексты

function yearInsight(D) {
  const Y = D.years.get(ST.year);
  const total = Y.photos + Y.videos;
  const peak = Y.months.indexOf(Math.max(...Y.months));
  let s = `В ${ST.year} году — <b>${shots(total)}</b>`;
  const C = ST.cmp ? D.years.get(ST.cmp) : null;
  if (C) {
    // год ещё идёт (это последний год в папке и последний снимок не 31 декабря) — сравниваем «к этому же дню»
    const last = D.days[D.days.length - 1];
    const ongoing = ST.year === +last.slice(0, 4) && last.slice(5) !== '12-31' && ST.cmp < ST.year;
    const sumTo = (y, md) => [...D.byDay].filter(([k]) => k.startsWith(y + '-') && k.slice(5) <= md).reduce((a, [, n]) => a + n, 0);
    const mine = ongoing ? sumTo(ST.year, last.slice(5)) : total;
    const theirs = ongoing ? sumTo(ST.cmp, last.slice(5)) : C.photos + C.videos;
    const md = `${+last.slice(8)} ${MONTHS_GEN[+last.slice(5, 7) - 1]}`;
    if (theirs) {
      const diff = Math.round((mine / theirs - 1) * 100);
      const word = diff > 0 ? `на ${diff} % больше` : diff < 0 ? `на ${-diff} % меньше` : 'столько же';
      s += ongoing ? `. К ${md} в ${ST.cmp} году было ${fmtN(theirs)} — сейчас ${word}` : `, ${word}, чем в ${ST.cmp}`;
    }
  }
  return s + `. Самый активный месяц — <b>${MONTHS_FULL[peak].toLowerCase()}</b> (${fmtN(Y.months[peak])}).`;
}

function timeInsight(D) {
  const total = D.hours.reduce((a, b) => a + b, 0) || 1;
  const peak = D.hours.indexOf(Math.max(...D.hours));
  const night = [22, 23, 0, 1, 2, 3, 4, 5].reduce((a, h) => a + D.hours[h], 0) / total;
  const morning = [5, 6, 7, 8].reduce((a, h) => a + D.hours[h], 0) / total;
  const wd = D.wdays.indexOf(Math.max(...D.wdays));
  const who = night > 0.12 ? 'Немного сова: ' + Math.round(night * 100) + ' % снимков сделаны ночью.'
    : morning > 0.12 ? 'Жаворонок: ' + Math.round(morning * 100) + ' % снимков — рано утром.' : '';
  return `Чаще всего — около <b>${peak}:00</b> и <b>${WEEKDAYS_ON[wd]}</b>. ${who}`;
}

function funFacts(D) {
  const facts = [];
  const first = D.days[0], last = D.days[D.days.length - 1];
  facts.push(`Первый снимок — <b>${fmtDay(first)}</b>, последний — <b>${fmtDay(last)}</b>.`);
  if (D.gap.n > 1) facts.push(`Самый долгий перерыв — <b>${pluralN(D.gap.n, 'день', 'дня', 'дней')}</b>: ${rangeText(D.gap.from, D.gap.to)}.`);
  const top = [...D.byDay].sort((a, b) => b[1] - a[1]).slice(0, 3);
  if (top.length > 1) facts.push('Самые активные дни: ' + top.map(([k, n]) => `<button class="link" data-act="st-day" data-day="${k}">${rangeText(k, k)}</button> (${fmtN(n)})`).join(', ') + '.');
  if (D.geo) facts.push(`С геометкой — ${shots(D.geo)} (${Math.round(D.geo / D.items.length * 100)} %), их видно на <button class="link" data-act="st-map">карте</button>.`);
  const ys = [...D.years.entries()].map(([y, Y]) => [y, Y.photos + Y.videos]);
  if (ys.length > 1) {
    const [by, bn] = ys.reduce((a, b) => (b[1] > a[1] ? b : a));
    facts.push(`Самый «фотографичный» год — <b>${by}</b>: ${shots(bn)}, в среднем ${fmtN(bn / 365 * 7)} в неделю.`);
  }
  return `<section class="card st-card st-facts"><div class="st-head"><h3>Интересное</h3></div><ul>${facts.map(f => `<li>${f}</li>`).join('')}</ul></section>`;
}

function monthTable(D, ys) {
  return `<table class="stats-table st-mtable"><thead><tr><th>Год</th>${MONTHS.map(m => `<th>${m}</th>`).join('')}<th>Всего</th></tr></thead><tbody>` +
    ys.slice().reverse().map(y => {
      const Y = D.years.get(y);
      return `<tr${y === ST.year ? ' class="cur"' : ''}><td>${y}</td>${Y.months.map(n => `<td>${n ? fmtN(n) : '—'}</td>`).join('')}<td>${fmtN(Y.photos + Y.videos)}</td></tr>`;
    }).join('') + '</tbody></table>';
}

// ------------------------------------------------------------ графики

const svgNS = 'http://www.w3.org/2000/svg';
/** Ось Y с круглыми делениями (1, 2, 2,5, 5 × 10ⁿ): 0 · 1 тыс. · 2 тыс., а не 625 · 1,3 тыс. */
function niceScale(v) {
  const raw = Math.max(v, 4) / 4, p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
  const step = Math.max(1, (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p);
  return { step, max: Math.ceil(Math.max(v, 1) / step) * step };
}
const tip = $('#stTip') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'stTip', className: 'st-tip hidden' }));
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.classList.remove('hidden');
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(innerWidth - r.width - 8, x + 14) + 'px';
  tip.style.top = Math.max(8, y - r.height - 10) + 'px';
}
const hideTip = () => tip.classList.add('hidden');

/** Сетка и подписи оси Y по круглым делениям niceScale. */
function yAxis({ max, step }, x0, x1, y0, h) {
  let g = '';
  for (let v = 0; v <= max + 1e-9; v += step) {
    const y = y0 + h - h * v / max;
    g += `<line class="st-grid${v ? '' : ' base'}" x1="${x0}" x2="${x1}" y1="${y}" y2="${y}"/><text class="st-tick" x="${x0 - 8}" y="${y + 4}" text-anchor="end">${fmtK(v)}</text>`;
  }
  return g;
}

/** Год к году: линия на каждый год, выбранный — синим с заливкой, сравнение — оранжевым, остальные — серым фоном. */
function drawYoY(D) {
  const box = $('#stYoY');
  const W = box.clientWidth, H = 260, L = 44, R = 56, T = 14, B = 26, w = W - L - R, h = H - T - B;
  const last = D.days[D.days.length - 1];
  const lastY = +last.slice(0, 4), lastM = +last.slice(5, 7) - 1;
  const series = new Map();
  for (const [y, Y] of D.years) {
    let vals = Y.months.slice();
    if (ST.mode === 'cum') { let a = 0; vals = vals.map(v => (a += v)); }
    series.set(y, y === lastY ? vals.slice(0, lastM + 1) : vals);  // текущий год — только до последнего месяца со снимками
  }
  const scale = niceScale(Math.max(...[...series.values()].flat())), max = scale.max;
  const X = i => L + (w * i) / 11, Yv = v => T + h - (h * v) / max;
  const path = vals => vals.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Yv(v).toFixed(1)}`).join('');
  let svg = yAxis(scale, L, L + w, T, h);
  svg += MONTHS.map((m, i) => `<text class="st-tick" x="${X(i)}" y="${H - 6}" text-anchor="middle">${m}</text>`).join('');
  for (const [y, vals] of series) if (y !== ST.year && y !== ST.cmp) svg += `<path class="st-line other" d="${path(vals)}"/>`;
  const label = (y, vals, cls) => {
    const i = vals.length - 1;
    return `<path class="st-line ${cls}" d="${path(vals)}"/><circle class="st-dot ${cls}" cx="${X(i)}" cy="${Yv(vals[i])}" r="4.5"/>
      <text class="st-end" x="${X(i) + 9}" y="${Yv(vals[i]) + 4}">${y}</text>`;
  };
  if (ST.cmp && series.has(ST.cmp)) svg += label(ST.cmp, series.get(ST.cmp), 's2');
  const mine = series.get(ST.year);
  svg += `<path class="st-area" d="${path(mine)}L${X(mine.length - 1)},${T + h}L${X(0)},${T + h}Z"/>` + label(ST.year, mine, 's1');
  svg += `<line class="st-cross hidden" y1="${T}" y2="${T + h}"/><rect class="st-hit" x="${L - 10}" y="${T}" width="${w + 20}" height="${h}"/>`;
  box.innerHTML = `<svg width="${W}" height="${H}">${svg}</svg>`;
  const others = series.size - 1 - (ST.cmp ? 1 : 0);
  // одна линия — легенда не нужна, год и так в заголовке и подписан у конца линии
  $('#stYoYLegend').innerHTML = series.size < 2 ? '' : `<span class="key line s1"></span>${ST.year}` + (ST.cmp ? `<span class="key line s2"></span>${ST.cmp}` : '') +
    (others > 0 ? `<span class="key line other"></span>другие годы` : '');

  const svgEl = $('svg', box), cross = $('.st-cross', box);
  svgEl.onmousemove = e => {
    const r = svgEl.getBoundingClientRect();
    const i = Math.max(0, Math.min(11, Math.round(((e.clientX - r.left - L) / w) * 11)));
    cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.classList.remove('hidden');
    const row = (y, cls) => { const v = series.get(y)?.[i]; return v == null ? '' : `<div><i class="key line ${cls}"></i><b>${fmtN(v)}</b> <span>${y}</span></div>`; };
    showTip(`<div class="st-tip-h">${MONTHS_FULL[i]}${ST.mode === 'cum' ? ', с начала года' : ''}</div>${row(ST.year, 's1')}${ST.cmp ? row(ST.cmp, 's2') : ''}
      <div class="st-tip-f">клик — открыть ${MONTHS_FULL[i].toLowerCase()} ${ST.year} в ленте</div>`, e.clientX, e.clientY);
    svgEl.dataset.m = i;
  };
  svgEl.onmouseleave = () => { cross.classList.add('hidden'); hideTip(); };
  svgEl.onclick = () => { if (svgEl.dataset.m != null) statsOpen(...monthRange(ST.year, +svgEl.dataset.m)); };
}

/** Каждый день года: недели столбцами (с понедельника), яркость — сколько снимков. */
function drawHeat(D) {
  const box = $('#stHeat');
  const y = ST.year, L = 26, T = 18;
  const jan1 = Date.UTC(y, 0, 1), lead = (new Date(jan1).getUTCDay() + 6) % 7;
  const ndays = (Date.UTC(y + 1, 0, 1) - jan1) / 864e5, weeks = Math.ceil((lead + ndays) / 7);
  const cell = Math.max(8, Math.min(16, Math.floor((box.clientWidth - L) / weeks) - 3)), step = cell + 3;
  const vals = [];
  for (let i = 0; i < ndays; i++) vals.push(D.byDay.get(keyOf(new Date(jan1 + i * 864e5))) || 0);
  // 4 уровня по квартилям дней со снимками: один день-рекорд не гасит остальные
  const nz = vals.filter(Boolean).sort((a, b) => a - b);
  const q = [0.25, 0.5, 0.75].map(p => nz[Math.floor(p * (nz.length - 1))] || 0);
  const lvl = n => (!n ? 0 : n <= q[0] ? 1 : n <= q[1] ? 2 : n <= q[2] ? 3 : 4);
  let svg = '';
  for (let i = 0; i < ndays; i++) {
    const p = lead + i, x = L + Math.floor(p / 7) * step, yy = T + (p % 7) * step;
    const k = keyOf(new Date(jan1 + i * 864e5));
    svg += `<rect class="hl${lvl(vals[i])}" x="${x}" y="${yy}" width="${cell}" height="${cell}" rx="2.5" data-k="${k}" data-n="${vals[i]}"/>`;
  }
  for (let m = 0; m < 12; m++) {
    const p = lead + (Date.UTC(y, m, 1) - jan1) / 864e5;
    svg += `<text class="st-tick" x="${L + Math.floor(p / 7) * step}" y="11">${MONTHS[m]}</text>`;
  }
  [0, 2, 4].forEach(d => { svg += `<text class="st-tick" x="0" y="${T + d * step + cell - 2}">${WEEKDAYS[d]}</text>`; });
  box.innerHTML = `<svg width="${L + weeks * step}" height="${T + 7 * step}">${svg}</svg>`;
  const svgEl = $('svg', box);
  svgEl.onmousemove = e => {
    const c = e.target.closest('rect[data-k]');
    if (!c) { hideTip(); return; }
    const n = +c.dataset.n, k = c.dataset.k;
    showTip(`<div><b>${n ? shots(n) : 'снимков нет'}</b></div><div class="st-tip-f">${fmtDay(k)}</div>`, e.clientX, e.clientY);
  };
  svgEl.onmouseleave = hideTip;
  svgEl.onclick = e => { const c = e.target.closest('rect[data-k]'); if (c && +c.dataset.n) statsOpen(c.dataset.k, c.dataset.k); };
}

/** По годам: столбцы фото + видео стопкой, клик — выбрать год. */
function drawYears(D) {
  const box = $('#stYears');
  const ys = [...D.years.keys()].sort((a, b) => a - b);
  const W = box.clientWidth, H = 230, L = 44, R = 8, T = 22, B = 26, w = W - L - R, h = H - T - B;
  const scale = niceScale(Math.max(...ys.map(y => D.years.get(y).photos + D.years.get(y).videos))), max = scale.max;
  const band = w / ys.length, bw = Math.min(24, band * 0.6);
  let svg = yAxis(scale, L, L + w, T, h);
  ys.forEach((y, i) => {
    const Y = D.years.get(y), cx = L + band * i + band / 2;
    const hp = h * Y.photos / max, hv = h * Y.videos / max;
    const top = T + h - hp - hv;
    // фото снизу, видео сверху; 2px просвет между ними, скругление только у верхнего края стопки
    if (hp) svg += `<path class="st-bar s1" d="${roundTop(cx - bw / 2, T + h - hp, bw, hp, hv ? 0 : 4)}"/>`;
    if (hv) svg += `<path class="st-bar s2" d="${roundTop(cx - bw / 2, top, bw, Math.max(0, hv - (hp ? 2 : 0)), 4)}"/>`;
    svg += `<text class="st-cap" x="${cx}" y="${top - 6}" text-anchor="middle">${fmtK(Y.photos + Y.videos)}</text>`;
    svg += `<text class="st-tick${y === ST.year ? ' cur' : ''}" x="${cx}" y="${H - 6}" text-anchor="middle">${ys.length > 12 ? `'${String(y).slice(2)}` : y}</text>`;
    svg += `<rect class="st-hit" x="${cx - band / 2}" y="${T}" width="${band}" height="${h + B}" data-y="${y}"/>`;
  });
  box.innerHTML = `<svg width="${W}" height="${H}">${svg}</svg>`;
  const svgEl = $('svg', box);
  svgEl.onmousemove = e => {
    const r = e.target.closest('rect[data-y]');
    if (!r) { hideTip(); return; }
    const Y = D.years.get(+r.dataset.y);
    showTip(`<div class="st-tip-h">${r.dataset.y}</div><div><i class="key line s1"></i><b>${fmtN(Y.photos)}</b> <span>фото</span></div>
      <div><i class="key line s2"></i><b>${fmtN(Y.videos)}</b> <span>видео</span></div><div class="st-tip-f">клик — выбрать год</div>`, e.clientX, e.clientY);
  };
  svgEl.onmouseleave = hideTip;
  svgEl.onclick = e => { const r = e.target.closest('rect[data-y]'); if (r) statsSet({ year: +r.dataset.y }); };
}

function roundTop(x, y, w, h, r) {
  r = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** Один ряд столбцов (часы, дни недели): одна серия, подписи оси снизу, значение — в подсказке. */
function drawColumns(box, vals, tick, name, H) {
  const W = box.clientWidth, L = 8, R = 8, T = 8, B = 20, w = W - L - R, h = H - T - B;
  const max = Math.max(1, ...vals), band = w / vals.length, bw = Math.min(24, band - 2);
  const peak = vals.indexOf(max);
  let svg = `<line class="st-grid base" x1="${L}" x2="${L + w}" y1="${T + h}" y2="${T + h}"/>`;
  vals.forEach((v, i) => {
    const cx = L + band * i + band / 2, bh = h * v / max;
    if (v) svg += `<path class="st-bar s1${i === peak ? '' : ' soft'}" d="${roundTop(cx - bw / 2, T + h - bh, bw, bh, 3)}"/>`;
    const t = tick(i);
    if (t) svg += `<text class="st-tick" x="${cx}" y="${H - 5}" text-anchor="middle">${t}</text>`;
    svg += `<rect class="st-hit" x="${cx - band / 2}" y="${T}" width="${band}" height="${h}" data-i="${i}"/>`;
  });
  box.innerHTML = `<svg width="${W}" height="${H}">${svg}</svg>`;
  const svgEl = $('svg', box), total = vals.reduce((a, b) => a + b, 0) || 1;
  svgEl.onmousemove = e => {
    const r = e.target.closest('rect[data-i]');
    if (!r) { hideTip(); return; }
    const i = +r.dataset.i;
    showTip(`<div><b>${fmtN(vals[i])}</b> <span>(${Math.round(vals[i] / total * 100)} %)</span></div><div class="st-tip-f">${name(i)}</div>`, e.clientX, e.clientY);
  };
  svgEl.onmouseleave = hideTip;
}

/** Горизонтальные полосы (что снято, чем снимали): первые 7, остальное — «другие». */
function drawHBars(box, rows) {
  rows.sort((a, b) => b[1] - a[1]);
  if (rows.length > 7) rows = [...rows.slice(0, 6), ['другие', rows.slice(6).reduce((a, r) => a + r[1], 0)]];
  const max = Math.max(...rows.map(r => r[1])), total = rows.reduce((a, r) => a + r[1], 0) || 1;
  box.innerHTML = `<div class="st-hbars">${rows.map(([name, n]) => `<div class="st-hrow" title="${esc(name)}: ${fmtN(n)} (${Math.round(n / total * 100)} %)">
      <span class="st-hname">${esc(name)}</span><span class="st-htrack"><i style="width:${(n / max * 100).toFixed(1)}%"></i></span><span class="st-hval">${fmtN(n)}</span></div>`).join('')}</div>`;
}

// ------------------------------------------------------------ действия

function statsSet(patch) {
  Object.assign(ST, patch);
  prefs.set('stYear', ST.year); prefs.set('stCmp', ST.cmp); prefs.set('stMode', ST.mode);
  const sc = $('#view-stats').scrollTop;
  renderStats();
  $('#view-stats').scrollTop = sc;
}

function statsOpen(from, to) {
  hideTip();
  S.from = from; S.to = to;
  CAL.anchor = null;
  S.view = 'grid';
  filtersChanged();
  $('#view-grid').scrollTop = 0;
}

function statsAction(act, el) {
  switch (act) {
    case 'st-year': statsSet({ year: +el.dataset.y }); return true;
    case 'st-cmp': statsSet({ cmp: +el.dataset.y }); return true;
    case 'st-mode': statsSet({ mode: el.dataset.m }); return true;
    case 'st-day': statsOpen(el.dataset.day, el.dataset.day); return true;
    case 'st-range': statsOpen(el.dataset.from, el.dataset.to); return true;
    case 'st-map': hideTip(); setView('map'); return true;
    case 'st-reset': resetFilters(); return true;
    default: return false;
  }
}

// перерисовка под новую ширину (окно, свёрнутая панель)
new ResizeObserver(() => { clearTimeout(drawStats.t); drawStats.t = setTimeout(drawStats, 120); }).observe($('#main'));
