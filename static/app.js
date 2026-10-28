'use strict';

// ============================================================ утилиты
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// emoji — в фишках фильтров вместо цветной точки: категорию видно с одного взгляда, а не по цвету.
// Подобраны по виду на тёмном фоне в Windows: 👥, 🐾 и ❔ там почти не видны
const CATS = {
  photo: { label: 'Фото с камеры', color: '#4f9cf9', emoji: '📷' },
  screenshot: { label: 'Скриншоты', color: '#f2b33d', emoji: '📱' },
  video: { label: 'Видео', color: '#e86a9a', emoji: '🎬' },
  messenger: { label: 'Из мессенджеров', color: '#3fb871', emoji: '💬' },
  other: { label: 'Прочее', color: '#8b929b', emoji: '🗂️' },
};
// Цвета смысловых категорий (страница «AI»): по порядку из /api/state, «не определено» — серое
const AI_COLORS = ['#f59e8b', '#e0a458', '#f472b6', '#4ade80', '#60a5fa', '#94a3b8', '#facc15', '#22d3ee', '#c084fc', '#a8a29e'];
const AI_EMOJI = {
  people: '🧑‍🤝‍🧑', animals: '🐱', food: '🍜', nature: '🌿', city: '🏙️', transport: '🚗',
  docs: '🧾', screens: '💻', memes: '😂', junk: '🌀',
};
const AI_OTHER = { key: 'other', label: 'Не определено', color: '#6b7280', emoji: '🤷' };
// Оценка кадра (страница «AI»): «лучшие» — красота кадра по SigLIP не ниже порога (≈ лучшие 7 % снимков из поездки)
const AES_BEST = 0.25;
const QUALITY_CATS = [
  { key: 'q:closed', label: 'Закрыты глаза', color: '#fb7185', emoji: '😴', hint: 'У кого-то в кадре закрыты глаза', test: it => it.qc > 0 },
  { key: 'q:best', label: 'Лучшие кадры', color: '#fbbf24', emoji: '✨', hint: 'Самые красивые снимки по оценке нейросети', test: it => it.qa >= AES_BEST },
];
const aiMatch = (it, k) => { const q = QUALITY_CATS.find(c => c.key === k); return q ? q.test(it) : it.ai === k; };
const PHONE_ICON = '<svg class="i"><use href="#i-phone"/></svg>';
const TS_SOURCE = { exif: 'из EXIF', name: 'из имени файла', mtime: 'дата изменения файла' };
const PLAYABLE = new Set(['.mp4', '.m4v', '.webm', '.mov']);

const fmtDayF = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' });
const fmtDTF = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const fmtTimeF = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit' });

const dayKey = t => (t == null ? 'none' : new Date(t * 1000).toISOString().slice(0, 10));
const fmtDay = k => (k === 'none' ? 'Без даты' : fmtDayF.format(new Date(k + 'T00:00:00Z')));
const fmtDT = t => (t == null ? '—' : fmtDTF.format(new Date(t * 1000)));
const fmtTime = t => {
  if (t == null) return '';
  const cs = Math.round((t % 1) * 100);
  return fmtTimeF.format(new Date(t * 1000)) + (cs ? ',' + String(cs).padStart(2, '0') : '');
};
const fmtSize = b => {
  if (!b) return '0 Б';
  const u = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return (i ? b.toFixed(b < 10 ? 1 : 0).replace('.', ',') : b) + ' ' + u[i];
};
const fmtDur = s => {
  if (!s) return '';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
};
const plural = (n, a, b, c) => {
  const m10 = n % 10, m100 = n % 100;
  return `${n} ${m10 === 1 && m100 !== 11 ? a : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? b : c}`;
};
const fileName = it => it.p.split('/').pop();
const folderName = it => it.p.includes('/') ? it.p.slice(0, it.p.lastIndexOf('/')) : '(корень)';
const thumbUrl = it => `/api/thumb/${it.id}?v=${it.v}`;
const phoneThumbUrl = it => `/api/phone-thumb/${it.id}?s=${S.find?.stamp || ''}`;
const fileUrl = it => `/api/file/${it.id}?v=${it.v}`;
const seriesColor = sid => `hsl(${(sid * 137.508) % 360} 68% 62%)`;

async function api(url, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(url, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, err = false, ms = 3800) {
  const el = document.createElement('div');
  el.className = 'toast' + (err ? ' err' : '');
  el.innerHTML = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), ms);
  return el;
}

function confirmModal(title, html, okText = 'OK', danger = false, cancel = true) {
  return new Promise(resolve => {
    const m = $('#modal');
    $('#mTitle').textContent = title;
    $('#mBody').innerHTML = html;
    const ok = $('#mOk');
    ok.textContent = okText;
    ok.className = 'btn ' + (danger ? 'danger' : 'primary');
    $('#mCancel').classList.toggle('hidden', !cancel);
    m.classList.remove('hidden');
    const done = v => { m.classList.add('hidden'); ok.onclick = null; $('#mCancel').onclick = null; M.resolve = null; resolve(v); };
    ok.onclick = () => done(true);
    $('#mCancel').onclick = () => done(false);
    M.resolve = done;
    ok.focus();
  });
}
const M = { resolve: null };

const prefs = {
  get(k, d) { try { const v = localStorage.getItem('mv.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('mv.' + k, JSON.stringify(v)); } catch { /* ignore */ } },
};

// ============================================================ состояние
const S = {
  root: null, items: [], byId: new Map(), series: new Map(), dups: new Map(),
  view: 'grid', types: new Set(prefs.get('types', [])), status: 'all', from: '', to: '',
  sbFolded: new Set(prefs.get('sbFolded', [])),  // свёрнутые группы левой панели
  aiMore: false,  // «Что на снимке»: показаны все категории, а не только частые
  sort: prefs.get('sort', 'desc'), collapse: prefs.get('collapse', false), cols: prefs.get('cols', 6),
  onlyOpen: prefs.get('onlyOpen', true),
  folded: new Set(prefs.get('folded', [])),  // свёрнутые дни / группы ленты
  list: [], selected: new Set(), focus: null, anchor: null,
  undo: [], settings: null, trashTarget: 'recycle',
  folder: { size: null, files: null },
  source: null, match: null, journal: 0,
  find: { phase: 'idle' },
  phoneFilter: 'all',  // корзина: all | found | missing
  ai: { ready: false, labels: [] },  // нейросеть: установлена ли модель, список смысловых категорий
  aiInfo: null,  // состояние страницы «AI» (/api/ai)
  aiSel: new Set(),  // выбранные смысловые категории (пусто — без фильтра)
  search: null,  // { q, rank: Map(id → место в выдаче), title? } — поиск по описанию или «похожие на…»
  cleanOpen: new Set(),  // «Можно удалить»: развёрнутые группы
};
const MATCH = {
  deleted: { label: 'Удалены в облаке', color: '#ef5350', hint: 'Вы уже удалили их из облака — сразу помечены к удалению' },
  cloud: { label: 'Есть в облаке', color: '#4f9cf9', hint: 'Копия уже сохранена в облаке' },
  new: { label: 'Новые', color: '#8b929b', hint: 'В облаке их нет — разберите их' },
};
let mapDirty = true;

// ============================================================ загрузка
let pollTimer = null, lastVersion = -1, lastLoad = 0, loadCost = 0, wasScanning = false;

async function init() {
  bindUI();
  try {
    applyState(await api('/api/state'));
  } catch (e) {
    toast('Сервер недоступен: ' + esc(e.message), true);
  }
}

function applyState(st) {
  S.root = st.root;
  S.source = st.source;
  S.journal = st.journal || 0;
  if (S.source?.kind !== 'phone') S.match = null;
  S.settings = st.settings;
  S.trashTarget = st.trash_target || 'recycle';
  if (st.ai) S.ai = st.ai;
  // в панели — только имя папки, полный путь во всплывающей подсказке
  $('#rootPath').textContent = S.root ? S.root.split(/[\\/]/).filter(Boolean).pop() : '';
  $('#matchSec').classList.toggle('hidden', S.source?.kind !== 'phone');
  $('#rootPath').style.direction = S.source?.kind === 'phone' ? 'ltr' : '';
  $('#rootPath').title = S.root || '';
  $('#btnRescan').disabled = !S.root;
  showWelcome(!S.root);
  renderRecent(st.recent || []);
  if (st.settings) {
    $('#setGap').value = st.settings.series_gap;
    $('#setDist').value = st.settings.series_dist;
    $('#setDistVal').textContent = st.settings.series_dist;
    $('#setKeep').value = st.settings.series_keep;
  }
  const missing = [];
  if (st.tools && !st.tools.ffmpeg) missing.push('ffmpeg (превью видео)');
  if (st.tools && !st.tools.heif) missing.push('pillow-heif (HEIC)');
  if (st.root && !('source' in st) && !applyState.oldServer) {
    applyState.oldServer = true;
    toast('Программа обновилась, а сервер работает со старой версией. Закройте окно <b>run.bat</b> и запустите его снова.', true, 15000);
  }
  if (missing.length && !applyState.warned) { applyState.warned = true; toast('Не найдено: ' + missing.join(', '), true, 7000); }
  if (st.scan) handleScan(st.scan);
}

function showWelcome(on) {
  $('#welcome').classList.toggle('hidden', !on);
  $('#welcomeBack')?.classList.toggle('hidden', !S.root);
}

function renderRecent(list) {
  $('#recentList').innerHTML = list.length
    ? '<h5>Недавние папки</h5>' + list.map(p => `<button data-path="${esc(p)}" title="${esc(p)}">${esc(p)}</button>`).join('')
    : '';
}

function handleScan(sc) {
  if (sc.folder_size != null) { S.folder = { size: sc.folder_size, files: sc.folder_files }; updateFolderStats(); }
  const box = $('#scanBox');
  if (sc.scanning) {
    wasScanning = true;
    box.classList.remove('hidden');
    const det = sc.total > 0 && ['analyze', 'hash', 'copy', 'list'].includes(sc.phase);
    box.classList.toggle('indeterminate', !det);
    $('.bar i', box).style.width = det ? (sc.done / sc.total * 100).toFixed(1) + '%' : '';
    const text = {
      walk: sc.message,
      analyze: `Анализ: ${sc.done} из ${sc.total}${sc.errors ? ` · ошибок ${sc.errors}` : ''}`,
      hash: `Поиск точных копий: ${sc.done} из ${sc.total}`,
      group: 'Поиск серий и дубликатов…',
      copy: `Копирование с телефона: ${sc.done} из ${sc.total}`,
      list: `${sc.message}: ${sc.done} из ${sc.total}`,
      match: sc.message,
    }[sc.phase] || sc.message;
    $('.scan-text', box).textContent = text;
    // на больших папках загрузка и перерисовка ленты дорогие — обновляем реже, чтобы не мешать сканированию
    if (sc.version !== lastVersion && Date.now() - lastLoad > Math.max(4000, loadCost * 8)) { lastVersion = sc.version; loadItems(); }
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => { try { handleScan(await api('/api/scan')); } catch { /* сервер перезапускается */ } }, 700);
  } else {
    box.classList.add('hidden');
    if (sc.version !== lastVersion || !S.items.length) {
      lastVersion = sc.version;
      loadItems();
    }
    if (wasScanning) {
      wasScanning = false;
      if (sc.phase === 'done') {
        const notes = [];
        if (sc.errors) notes.push(`не прочитано файлов: ${sc.errors}`);
        if (sc.cloud) notes.push(`не скачаны из облака: ${sc.cloud} — они без миниатюр, после скачивания пересканируйте папку`);
        toast('Сканирование завершено' + (notes.length ? ', ' + notes.join('; ') : ''), false, sc.cloud ? 10000 : undefined);
      }
    }
    if (sc.phase === 'error') toast(esc(sc.message), true, 8000);
    if (sc.phase === 'confirm' && !handleScan.asked) {
      handleScan.asked = true;
      confirmCopy(sc);
    }
  }
}

async function loadItems() {
  if (!S.root) return;
  lastLoad = Date.now();
  let items;
  try { items = await api('/api/items'); } catch (e) { toast('Не удалось загрузить список: ' + esc(e.message), true); return; }
  S.items = items;
  S.byId = new Map(items.map(i => [i.id, i]));
  S.series = new Map();
  S.dups = new Map();
  for (const it of items) {
    if (it.s) (S.series.get(it.s) || S.series.set(it.s, []).get(it.s)).push(it);
    if (it.d) (S.dups.get(it.d) || S.dups.set(it.d, []).get(it.d)).push(it);
  }
  for (const arr of S.series.values()) arr.sort((a, b) => a.t - b.t || a.id - b.id);
  for (const id of [...S.selected]) if (!S.byId.has(id)) S.selected.delete(id);
  if (S.focus && !S.byId.has(S.focus)) S.focus = null;
  mapDirty = true;
  renderCats();
  render();
  loadCost = Date.now() - lastLoad;
  if (S.source?.kind === 'folder' && S.find.phase === 'idle') {
    api('/api/phone-find').then(f => {
      if (f.phase === 'idle') return;
      S.find = f;
      if (findRunning()) pollFind(); else if (S.view === 'trash') rerenderKeepScroll();
    }).catch(() => {});
  }
}

// ============================================================ фильтры
function passFilters(it, useStatus = true, useDates = true) {
  if (S.types.size && !S.types.has(it.c)) return false;
  if (S.aiSel.size && ![...S.aiSel].some(k => aiMatch(it, k))) return false;
  if (S.search && !S.search.rank.has(it.id)) return false;
  if (S.match && (it.m || 'new') !== S.match) return false;
  if (useStatus && S.status !== 'all' && it.st !== +S.status) return false;
  if (useDates && (S.from || S.to)) {
    const k = dayKey(it.t);
    if (S.from && k < S.from) return false;
    if (S.to && k > S.to) return false;
  }
  return true;
}

function sortList(list) {
  if (S.sort === 'size') return list.sort((a, b) => (b.sz || 0) - (a.sz || 0) || (b.t ?? 0) - (a.t ?? 0));
  const d = S.sort === 'desc' ? -1 : 1;
  return list.sort((a, b) => ((a.t ?? 0) - (b.t ?? 0)) * d || (a.id - b.id) * d);
}

// Группы ленты: по дням, а при сортировке по размеру — по «весовым категориям»
const SIZE_BUCKETS = [
  [1024 ** 3, 'Больше 1 ГБ'], [100 * 1024 ** 2, '100 МБ – 1 ГБ'], [10 * 1024 ** 2, '10 – 100 МБ'],
  [1024 ** 2, '1 – 10 МБ'], [0, 'Меньше 1 МБ'],
];
// Масштаб ленты как в «Фото» на iPhone: чем больше снимков в ряду, тем крупнее группы
const ZOOM_LEVELS = [2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 18, 22, 26, 30];
const GRAN_COLS = { day: 5, month: 10, year: 18 };
const granularity = () => (S.cols <= 7 ? 'day' : S.cols <= 15 ? 'month' : 'year');
const fmtMonthF = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', month: 'long', year: 'numeric' });

function groupKey(it) {
  if (S.search && S.view === 'grid') return 'q';
  if (S.sort === 'size') return 'sz' + SIZE_BUCKETS.findIndex(([min]) => (it.sz || 0) >= min);
  const d = dayKey(it.t);
  if (d === 'none') return d;
  const g = granularity();
  return g === 'day' ? d : g === 'month' ? 'm' + d.slice(0, 7) : 'y' + d.slice(0, 4);
}
function groupTitle(k) {
  if (k === 'q') return S.search.title ? esc(S.search.title) : `По запросу «${esc(S.search.q)}»`;
  if (k.startsWith('sz')) return SIZE_BUCKETS[+k.slice(2)][1];
  if (k.startsWith('m')) return fmtMonthF.format(new Date(k.slice(1) + '-01T00:00:00Z')).replace(/\s*г\.$/, '');
  if (k.startsWith('y')) return k.slice(1);
  return fmtDay(k);
}
const groupSelectLabel = k => (k.startsWith('sz') || k === 'q' ? 'выбрать все' : k.startsWith('m') ? 'выбрать месяц'
  : k.startsWith('y') ? 'выбрать год' : 'выбрать день');

function seriesRep(sid) {
  const arr = S.series.get(sid) || [];
  return arr.find(i => i.st === 1) || arr.find(i => i.sb) || arr[0];
}

function seriesOrder() {
  const firstT = new Map();
  for (const it of S.items) {
    if (!it.s || !passFilters(it, false)) continue;
    if (!firstT.has(it.s) || it.t < firstT.get(it.s)) firstT.set(it.s, it.t);
  }
  const d = S.sort === 'asc' ? 1 : -1;
  return [...firstT.keys()].sort((a, b) => (firstT.get(a) - firstT.get(b)) * d);
}

const seriesDone = sid => (S.series.get(sid) || []).every(i => i.st !== 0);

function updateCounts() {
  const base = S.items.filter(it => passFilters(it, false));
  const set = (id, v) => { $('#cnt-' + id).textContent = v || ''; };
  set('grid', base.length);
  set('series', new Set(base.filter(i => i.s).map(i => i.s)).size);
  set('dups', new Set(base.filter(i => i.d).map(i => i.d)).size);
  set('map', base.filter(i => i.lat != null).length);
  set('clean', cleanSections().reduce((n, sec) => n + sec.items.length, 0));
  const tr = S.items.filter(i => i.st === 2).length;
  set('trash', tr);
  $('#trashCount').textContent = tr;
  $('#btnTrashTop').classList.toggle('has-items', tr > 0);
  $('#btnUndo').disabled = !S.undo.length;
  updateSelInfo();
  updateFolderStats();
}

function updateFolderStats() {
  const el = $('#folderStats');
  if (!S.root) { el.innerHTML = ''; return; }
  const mediaSize = S.items.reduce((s, i) => s + (i.sz || 0), 0);
  const f = S.folder;
  const parts = [`<b>${fmtSize(f.size ?? mediaSize)}</b>`];
  if (S.items.length) parts.push(plural(S.items.length, 'фото и видео', 'фото и видео', 'фото и видео'));
  el.innerHTML = parts.join(' · ');
  const rest = f.size != null ? f.size - mediaSize : null;
  el.title = [
    f.size != null ? `Вся папка: ${fmtSize(f.size)}, ${plural(f.files, 'файл', 'файла', 'файлов')} (без служебных папок и _rejected)` : '',
    `Фото и видео: ${plural(S.items.length, 'снимок', 'снимка', 'снимков')}, ${fmtSize(mediaSize)} (вместе с RAW и Live-видео)`,
    rest > 0 ? `Прочие файлы: ${fmtSize(rest)}` : '',
  ].filter(Boolean).join('\n');
}

function updateSelInfo() {
  const n = S.selected.size;
  const selSize = n > 1 ? [...S.selected].reduce((s, id) => s + (S.byId.get(id)?.sz || 0), 0) : 0;
  $('#selInfo').innerHTML = n > 1 ? `Выбрано: <b>${n}</b> (${fmtSize(selSize)}) · <kbd>P</kbd> оставить · <kbd>X</kbd> удалить${n <= 6 ? ' · <kbd>C</kbd> сравнить' : ''}` : '';
}

function renderMatch() {
  if (S.source?.kind !== 'phone') return;
  const counts = { deleted: 0, cloud: 0, new: 0 };
  for (const it of S.items) counts[it.m || 'new']++;
  $('#matchList').innerHTML = Object.entries(MATCH).map(([k, m]) =>
    `<div class="cat ${S.match === k ? 'active' : ''}" data-match="${k}" title="${m.hint}. Клик — показать только их">
       <span class="dot" style="background:${m.color}"></span>${m.label}<span class="cnt">${counts[k] || ''}</span></div>`).join('');
  const pending = S.items.filter(i => i.m === 'cloud' && i.st !== 2).length;
  $('#btnMarkCloud').disabled = !pending;
  $('#btnMarkCloud').textContent = pending ? `«Есть в облаке» → к удалению (${pending})` : '«Есть в облаке» → к удалению';
}

function aiLabels() {
  return [...S.ai.labels.map((l, i) => ({ ...l, color: AI_COLORS[i % AI_COLORS.length], emoji: AI_EMOJI[l.key] })), AI_OTHER];
}

const AI_TOP = 6;  // «Что на снимке»: сколько частых категорий видно сразу, остальные — по «ещё»
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

/** Значок категории: эмодзи, а если его нет — цветная точка. mark — категория с полями emoji / color (или null). */
const markHTML = m => (m?.emoji ? `<span class="emo">${m.emoji}</span>` : m?.color ? `<span class="dot" style="background:${m.color}"></span>` : '');
const chipHTML = (key, attr, label, mark, n, active, title = '') =>
  `<button class="chip${active ? ' active' : ''}" ${attr}="${key}" title="${esc(title)}">${markHTML(mark)}${label}${n != null ? `<span class="cnt">${n}</span>` : ''}</button>`;

function renderAiSide() {
  const counts = {};
  let rated = false;
  for (const it of S.items) {
    if (it.ai) counts[it.ai] = (counts[it.ai] || 0) + 1;
    if (it.qf != null || it.qa != null) rated = true;
    for (const q of QUALITY_CATS) if (q.test(it)) counts[q.key] = (counts[q.key] || 0) + 1;
  }
  const labels = aiLabels().filter(l => counts[l.key]).sort((a, b) => counts[b.key] - counts[a.key]);
  const any = labels.length > 0 || rated;
  $('#aiSec').classList.toggle('hidden', !any);
  if (!any) return;
  // частые сверху; выбранные видны всегда, даже если они не из частых
  const shown = S.aiMore ? labels : labels.filter((l, i) => i < AI_TOP || S.aiSel.has(l.key));
  const hidden = labels.length - shown.length;
  $('#aiCats').innerHTML = shown.map(l => chipHTML(l.key, 'data-ai', l.label, l, counts[l.key], S.aiSel.has(l.key), 'Клик — показать только их, можно выбрать несколько')).join('') +
    (hidden ? `<button class="chip more" data-ai-more>ещё ${hidden}</button>` : S.aiMore && labels.length > AI_TOP ? '<button class="chip more" data-ai-more>свернуть</button>' : '');
  const quality = QUALITY_CATS.filter(q => counts[q.key]);
  $('#qCats').innerHTML = quality.length ? '<span class="chips-label">Оценка кадра</span>' +
    quality.map(q => chipHTML(q.key, 'data-ai', q.label, q, counts[q.key], S.aiSel.has(q.key), q.hint)).join('') : '';
  const sel = [...S.aiSel].map(k => aiLabels().find(l => l.key === k)?.label || QUALITY_CATS.find(q => q.key === k)?.label).filter(Boolean);
  $('#aiVal').textContent = sel.length === 1 ? sel[0] : sel.length ? `выбрано ${sel.length}` : '';
}

/** Год и месяц по дате снимка (время в индексе — «настенное», как в EXIF, поэтому UTC). */
const ym = t => { const d = new Date(t * 1000); return [d.getUTCFullYear(), d.getUTCMonth()]; };
const pad2 = n => String(n).padStart(2, '0');
const monthRange = (y, m) => [`${y}-${pad2(m + 1)}-01`, `${y}-${pad2(m + 1)}-${pad2(new Date(Date.UTC(y, m + 1, 0)).getUTCDate())}`];

/** Какой год / месяц / день сейчас выбран быстрыми кнопками (null — свои даты или весь период). */
function periodPick() {
  if (!S.from || !S.to) return null;
  const y = +S.from.slice(0, 4), m = +S.from.slice(5, 7) - 1;
  if (S.from === `${y}-01-01` && S.to === `${y}-12-31`) return { y, m: null, d: null };
  if (S.from === S.to) return { y, m, d: +S.from.slice(8, 10) };
  const [a, b] = monthRange(y, m);
  return S.from === a && S.to === b ? { y, m, d: null } : null;
}

/** Быстрые кнопки периода: годы → месяцы выбранного года. Дни и произвольный период — в календаре под ними.
 *  Уровень, где выбирать не из чего (один год, один месяц), пропускается. */
function renderPeriod() {
  const tree = new Map();  // год → месяц → множество дней, где есть снимки
  for (const it of S.items) {
    if (it.t == null) continue;
    const d = new Date(it.t * 1000);
    const y = d.getUTCFullYear(), m = d.getUTCMonth();
    const months = tree.get(y) || tree.set(y, new Map()).get(y);
    (months.get(m) || months.set(m, new Set()).get(m)).add(d.getUTCDate());
  }
  const pick = periodPick();
  const ys = [...tree.keys()].sort((a, b) => b - a);
  const year = pick?.y ?? (ys.length === 1 ? ys[0] : null);
  const months = year != null ? tree.get(year) : null;
  let html = ys.length > 1 ? ys.map(y => chipHTML(y, 'data-year', y, null, null, pick?.y === y && pick.m == null)).join('') : '';
  if (months?.size > 1) {
    html += `<div class="chips months">${[...months.keys()].sort((a, b) => a - b).map(m =>
      chipHTML(`${year}-${m}`, 'data-month', MONTHS[m], null, null, pick?.y === year && pick.m === m && pick.d == null)).join('')}</div>`;
  }
  $('#periodChips').innerHTML = html;
  // Календарь открыт, если кнопок нет (поездка за один месяц) или выбраны дни, а не год / месяц целиком
  const custom = (S.from || S.to) && (!pick || pick.d);
  CAL.auto = !html || custom;
  $('#periodVal').textContent = pick && !pick.d ? (pick.m == null ? String(pick.y) : `${MONTHS[pick.m]} ${pick.y}`)
    : S.from || S.to ? rangeText(S.from, S.to) : '';
  renderCalendar();
}

// ============================================================ календарь периода
// Дни со снимками подсвечены (чем больше снимков, тем ярче). Период — двумя кликами: начало и конец.
// mode: days — дни месяца, months — месяцы года (клик по заголовку), years — все годы со снимками
const CAL = { open: prefs.get('calOpen', false), auto: false, mode: 'days', month: null, year: null, anchor: null, counts: new Map() };
const MONTHS_FULL = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/** «15–20 мар 2026», «28 фев – 3 мар 2026», «20 мар 2026». */
function rangeText(a, b) {
  const p = k => (k ? { y: +k.slice(0, 4), m: +k.slice(5, 7) - 1, d: +k.slice(8, 10) } : null);
  const x = p(a), y = p(b);
  if (!x || !y) return x ? `с ${x.d} ${MONTHS[x.m]} ${x.y}` : `по ${y.d} ${MONTHS[y.m]} ${y.y}`;
  if (a === b) return `${x.d} ${MONTHS[x.m]} ${x.y}`;
  if (x.y === y.y && x.m === y.m) return `${x.d}–${y.d} ${MONTHS[x.m]} ${x.y}`;
  if (x.y === y.y) return `${x.d} ${MONTHS[x.m]} – ${y.d} ${MONTHS[y.m]} ${x.y}`;
  return `${x.d} ${MONTHS[x.m]} ${x.y} – ${y.d} ${MONTHS[y.m]} ${y.y}`;
}

function renderCalendar() {
  const el = $('#calendar');
  const open = CAL.open || CAL.auto;
  el.classList.toggle('hidden', !open);
  $('#calToggle').classList.toggle('hidden', CAL.auto);
  $('#calToggle').textContent = CAL.open ? 'Скрыть календарь' : 'Календарь';
  if (!open) return;
  // Снимки по дням — с учётом остальных фильтров, но без периода: видно, где искать
  const counts = CAL.counts = new Map();
  for (const it of S.items) if (it.t != null && passFilters(it, true, false)) {
    const k = dayKey(it.t);
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  const keys = [...counts.keys()].sort();
  if (!keys.length) { el.innerHTML = '<div class="cal-empty">Нет снимков с датой</div>'; return; }
  const first = keys[0].slice(0, 7), last = keys[keys.length - 1].slice(0, 7);
  if (!CAL.month || CAL.month < first || CAL.month > last) CAL.month = (S.to || S.from || keys[keys.length - 1]).slice(0, 7);
  if (CAL.month < first) CAL.month = first;
  if (CAL.month > last) CAL.month = last;
  const bounds = { first, last, y0: +first.slice(0, 4), y1: +last.slice(0, 4) };
  const sum = prefix => keys.filter(k => k.startsWith(prefix)).reduce((s, k) => s + counts.get(k), 0);
  const body = CAL.mode === 'years' ? calYears(bounds, sum) : CAL.mode === 'months' ? calMonths(bounds, sum) : calDays(bounds, counts);

  const sel = S.from || S.to;
  const inRange = keys.filter(k => (!S.from || k >= S.from) && (!S.to || k <= S.to)).reduce((s, k) => s + counts.get(k), 0);
  const [my, mm] = [+CAL.month.slice(0, 4), +CAL.month.slice(5, 7) - 1];
  const wholeMonth = monthRange(my, mm);
  const monthLink = CAL.mode === 'days' && !(S.from === wholeMonth[0] && S.to === wholeMonth[1])
    ? `<button class="link" data-cal-month title="Выбрать ${MONTHS_FULL[mm].toLowerCase()} целиком">весь месяц</button>` : '';
  const foot = CAL.anchor
    ? `<span class="cal-hint">${CAL.mode === 'days' ? 'Теперь выберите последний день' : 'Найдите последний день'}</span><button class="link" data-cal-cancel>Отмена</button>`
    : sel ? `<span><b>${rangeText(S.from, S.to)}</b> · ${plural(inRange, 'снимок', 'снимка', 'снимков')}</span>${monthLink}<button class="link" data-cal-clear>Сбросить</button>`
      : `<span class="cal-hint">${CAL.mode === 'days' ? 'Клик — начало, второй — конец' : CAL.mode === 'months' ? 'Выберите месяц' : 'Выберите год'}</span>${monthLink}`;
  el.innerHTML = `${body}<div class="cal-foot">${foot}</div>`;
  if (CAL.mode === 'days') paintRange();
}

function calHead(prev, title, next, zoom, titleHint) {
  const nav = (dir, ok, hint) => `<button class="cal-nav" data-cal-nav="${dir}" ${ok ? '' : 'disabled'} title="${hint}"><svg class="i"><use href="#i-${dir < 0 ? 'left' : 'right'}"/></svg></button>`;
  return `<div class="cal-head">${prev ? nav(-1, prev.ok, prev.hint) : '<span class="cal-nav-gap"></span>'}
    <button class="cal-title${zoom ? ' zoom' : ''}" ${zoom ? 'data-cal-zoom' : 'disabled'} title="${titleHint || ''}">${title}</button>
    ${next ? nav(1, next.ok, next.hint) : '<span class="cal-nav-gap"></span>'}</div>`;
}

const heat = (n, max) => (n ? ' has l' + (1 + Math.min(3, Math.floor((n / max) * 4))) : '');  // яркость: 1–4

/** Пересекается ли отрезок [a, b] (даты «гггг-мм-дд») с выбранным периодом или с начатым выбором. */
function inSelection(a, b) {
  const from = CAL.anchor || S.from, to = CAL.anchor || S.to;
  if (!from && !to) return false;
  return (!from || b >= from) && (!to || a <= to);
}

function calDays({ first, last, y0, y1 }, counts) {
  const y = +CAL.month.slice(0, 4), m = +CAL.month.slice(5, 7) - 1;
  const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  const lead = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7;  // неделя с понедельника
  let max = 1;
  for (let d = 1; d <= days; d++) max = Math.max(max, counts.get(`${CAL.month}-${pad2(d)}`) || 0);
  let cells = WEEKDAYS.map(w => `<span class="cal-wd">${w}</span>`).join('') + '<span></span>'.repeat(lead);
  for (let d = 1; d <= days; d++) {
    const k = `${CAL.month}-${pad2(d)}`, n = counts.get(k) || 0;
    cells += `<button class="cal-day${heat(n, max)}" data-cal-day="${k}" title="${d} ${MONTHS_GEN[m]} ${y}${n ? ' · ' + plural(n, 'снимок', 'снимка', 'снимков') : ' · снимков нет'}">${d}</button>`;
  }
  return calHead({ ok: CAL.month > first, hint: 'Предыдущий месяц' }, `${MONTHS_FULL[m]} ${y}`,
    { ok: CAL.month < last, hint: 'Следующий месяц' }, true, 'Выбрать другой месяц или год') +
    `<div class="cal-grid">${cells}</div>`;
}

function calMonths({ first, last, y0, y1 }, sum) {
  const y = CAL.year;
  const n = Array.from({ length: 12 }, (_, m) => sum(`${y}-${pad2(m + 1)}`));
  const max = Math.max(1, ...n);
  const cells = n.map((c, m) => {
    const k = `${y}-${pad2(m + 1)}`, [a, b] = monthRange(y, m);
    const off = k < first || k > last;
    return `<button class="cal-cell${heat(c, max)}${inSelection(a, b) ? ' in' : ''}${k === CAL.month ? ' cur' : ''}" data-cal-open-month="${k}" ${off ? 'disabled' : ''}
      title="${MONTHS_FULL[m]} ${y}${c ? ' · ' + plural(c, 'снимок', 'снимка', 'снимков') : ' · снимков нет'}">${MONTHS[m]}<small>${c || ''}</small></button>`;
  }).join('');
  return calHead({ ok: y > y0, hint: 'Предыдущий год' }, String(y), { ok: y < y1, hint: 'Следующий год' }, y1 > y0, 'Выбрать год') +
    `<div class="cal-cells">${cells}</div>`;
}

function calYears({ y0, y1 }, sum) {
  const ys = Array.from({ length: y1 - y0 + 1 }, (_, i) => y0 + i);
  const n = ys.map(y => sum(String(y)));
  const max = Math.max(1, ...n);
  const cells = ys.map((y, i) => `<button class="cal-cell${heat(n[i], max)}${inSelection(`${y}-01-01`, `${y}-12-31`) ? ' in' : ''}${y === CAL.year ? ' cur' : ''}" data-cal-open-year="${y}"
      title="${y}${n[i] ? ' · ' + plural(n[i], 'снимок', 'снимка', 'снимков') : ' · снимков нет'}">${y}<small>${n[i] || ''}</small></button>`).join('');
  return calHead(null, y0 === y1 ? String(y0) : `${y0} – ${y1}`, null, false) + `<div class="cal-cells">${cells}</div>`;
}

/** Подсветка периода: выбранного, или от первого клика до дня под курсором. */
function paintRange(hover) {
  let a = S.from, b = S.to;
  if (CAL.anchor) [a, b] = hover && hover < CAL.anchor ? [hover, CAL.anchor] : [CAL.anchor, hover || CAL.anchor];
  for (const c of $$('#calendar .cal-day')) {
    const k = c.dataset.calDay;
    const on = (a || b) && (!a || k >= a) && (!b || k <= b);
    c.classList.toggle('in', !!on);
    c.classList.toggle('start', !!on && k === a);
    c.classList.toggle('end', !!on && k === b);
    c.classList.toggle('preview', !!CAL.anchor && !!on);
  }
}

function calShift(dir) {
  if (CAL.mode === 'months') {
    CAL.year += dir;
  } else {
    const y = +CAL.month.slice(0, 4), m = +CAL.month.slice(5, 7) - 1 + dir;
    const d = new Date(Date.UTC(y, m, 1));
    CAL.month = `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}`;
  }
  renderCalendar();
}

/** Сколько фильтров включено — для заголовка «Фильтры» и кнопки «Сбросить». */
function renderFilterSummary() {
  const n = (S.types.size ? 1 : 0) + (S.aiSel.size ? 1 : 0) + (S.status !== 'all' ? 1 : 0) + (S.from || S.to ? 1 : 0)
    + (S.search ? 1 : 0) + (S.match ? 1 : 0);
  $('#fltCount').textContent = n;
  $('#fltCount').classList.toggle('hidden', !n);
  $('#fltReset').classList.toggle('hidden', !n);
  $('#typeVal').textContent = S.types.size === 1 ? CATS[[...S.types][0]].label : S.types.size ? `выбрано ${S.types.size}` : '';
  $$('#statusFilter button').forEach(b => b.classList.toggle('active', b.dataset.v === S.status));
  $$('.sb-group').forEach(g => g.classList.toggle('folded', S.sbFolded.has(g.dataset.group)));
  const semantic = S.items.some(i => i.ai);
  $('#aiQuery').placeholder = semantic ? 'Поиск по смыслу и имени' : 'Поиск по имени файла';
  $('#aiQuery').title = (semantic ? 'Например: море, рамен, ночной город, чек или IMG_1234. ' : 'Часть имени файла, например IMG_1234. ') + 'Enter — искать, Esc — сбросить';
}

function resetFilters() {
  S.types.clear(); S.aiSel.clear(); S.status = 'all'; S.from = S.to = ''; S.match = null; S.search = null;
  CAL.anchor = null;
  $('#aiQuery').value = '';
  filtersChanged();
}

function renderCats() {
  renderMatch();
  renderAiSide();
  renderPeriod();
  const counts = {};
  for (const it of S.items) counts[it.c] = (counts[it.c] || 0) + 1;
  $('#cats').innerHTML = Object.entries(CATS).filter(([k]) => counts[k] || S.types.has(k)).map(([k, c]) =>
    chipHTML(k, 'data-cat', c.label, c, counts[k] || 0, S.types.has(k), 'Клик — показать только их, можно выбрать несколько')).join('');
  renderFilterSummary();
}

// ============================================================ отрисовка
function setView(v) {
  S.view = v;
  if (v === 'trash') S.stats = null;  // статистику перечитываем при каждом открытии корзины
  S.selected.clear();
  S.focus = null;
  $('#stTip')?.classList.add('hidden');  // подсказка графика статистики
  render();
  $('#view-' + v).scrollTop = 0;
}

function render() {
  updateCounts();
  $$('#views button').forEach(b => b.classList.toggle('active', b.dataset.view === S.view));
  $$('.view').forEach(v => v.classList.toggle('hidden', v.id !== 'view-' + S.view));
  // страница «AI» открывается и без папки: модель можно скачать заранее
  if (S.view === 'ai') { if (!S.root) showWelcome(false); renderAI(); return; }
  if (!S.root) { showWelcome(true); return; }
  // stats.js подключается отдельным файлом после app.js — обращаемся к нему только при отрисовке
  ({ grid: renderGrid, series: renderSeries, dups: renderDups, map: renderMap, trash: renderTrash, stats: () => renderStats(), clean: renderClean })[S.view]();
}

function tileHTML(it, o = {}) {
  const cls = ['tile', 'st-' + it.st];
  if (S.selected.has(it.id)) cls.push('sel');
  if (S.focus === it.id) cls.push('focus');
  let style = '';
  const b = [];
  if (it.s && !o.noSeries) {
    cls.push('ser');
    style = `--sc:${seriesColor(it.s)}`;
    if (o.nextSer) cls.push('ser-next');
    if (!o.prevSer || o.stack) {
      const n = S.series.get(it.s)?.length || 0;
      b.push(`<span class="bdg ser" data-ser="${it.s}" title="Серия из ${n} кадров — разобрать (S)"><svg class="i"><use href="#i-layers"/></svg>${n}</span>`);
    }
    if (o.stack) cls.push('stack');
  }
  if (it.sb && !o.noSeries) b.push('<span class="bdg best" title="Рекомендуется оставить (самый резкий)">★</span>');
  if (it.m === 'deleted') b.push(`<span class="bdg mdel${it.ms ? '' : ' weak'}" title="${it.ms ? 'Удалён' : 'Похоже, удалён'} в облаке: ${esc(it.mr)}">☁✕</span>`);
  else if (it.m === 'cloud') b.push(`<span class="bdg mcloud${it.ms ? '' : ' weak'}" title="${it.ms ? 'Есть' : 'Похоже, есть'} в облаке: ${esc(it.mr)}">☁</span>`);
  if (typeof it.ph === 'string') b.push(`<span class="bdg phone" title="Есть на телефоне: ${esc(it.ph)}">${PHONE_ICON}</span>`);
  else if (it.ph === 0) b.push(`<span class="bdg nophone" title="На телефоне не найден">${PHONE_ICON}<i>✕</i></span>`);
  else if (it.ph === -2) b.push(`<span class="bdg phmis" title="Не удалось прочитать файл с телефона для сверки — разблокируйте iPhone и повторите поиск">${PHONE_ICON}<i>?</i></span>`);
  else if (it.ph === -1) b.push(`<span class="bdg phmis" title="На телефоне есть файл того же размера и даты, но содержимое другое — его не удалим">${PHONE_ICON}<i>≠</i></span>`);
  if (it.qc > 0) b.push(`<span class="bdg eyes" title="Закрыты глаза: ${it.qc} из ${plural(it.qf, 'лица', 'лиц', 'лиц')}"><svg class="i"><use href="#i-eye-off"/></svg></span>`);
  if (it.d) b.push(`<span class="bdg dup" title="${it.dx ? 'Есть точная копия' : 'Есть похожий дубликат'}">⧉</span>`);
  for (const c of it.cx || []) b.push(`<span class="bdg cx" title="${c === 'RAW' ? 'Есть RAW-файл с тем же именем' : c === 'LIVE' ? 'Live Photo: есть видео с тем же именем' : 'Связанный файл'}">${c}</span>`);
  if (it.err) b.push(`<span class="bdg err" title="${esc(it.err)}">!</span>`);
  if (it.cl) b.push('<span class="bdg offline" title="Файл ещё не скачан из облака на компьютер, поэтому не разобран: нет миниатюры, серий и дубликатов. Когда он скачается, пересканируйте папку.">☁↓</span>');
  const dur = it.k === 'v' ? `<span class="bdg vid-dur">▶ ${fmtDur(it.dur)}</span>` : '';
  // в корзине у найденных на телефоне — миниатюра, которую прислал сам iPhone
  const inset = S.view === 'trash' && typeof it.ph === 'string'
    ? `<span class="ph-inset" title="Так этот файл выглядит на iPhone: ${esc(it.ph.split('/').pop())}"><img src="${phoneThumbUrl(it)}" alt="" onerror="this.parentNode.remove()"><i>iPhone</i></span>` : '';
  return `<div class="${cls.join(' ')}" data-id="${it.id}" style="${style}" title="${esc(fileName(it))} · ${fmtSize(it.sz)}">` +
    `<div class="img"><img loading="lazy" decoding="async" src="${thumbUrl(it)}" alt=""></div>` +
    `<div class="badges">${b.join('')}</div>${dur}${inset}<span class="tsz">${fmtSize(it.sz)}</span></div>`;
}

function daysHTML(list, o = {}) {
  const days = new Map();
  for (const it of list) {
    const k = groupKey(it);
    (days.get(k) || days.set(k, []).get(k)).push(it);
  }
  let html = '';
  for (const [k, items] of days) {
    const total = items.reduce((sum, i) => sum + (i.sz || 0), 0);
    const folded = o.foldable && S.folded.has(k);
    const fold = o.foldable
      ? `<button class="fold" data-act="fold" data-key="${k}" title="${folded ? 'Развернуть' : 'Свернуть'}"><svg class="i"><use href="#i-right"/></svg></button>` : '';
    const peek = folded
      ? `<span class="peek">${items.slice(0, 8).map(it => `<img loading="lazy" src="${thumbUrl(it)}" alt="">`).join('')}</span>` : '';
    html += `<section class="day${folded ? ' folded' : ''}"><div class="day-head${o.foldable ? ' foldable' : ''}" ${o.foldable ? `data-fold="${k}"` : ''}>${fold}<h3>${groupTitle(k)}</h3>` +
      `<span class="sub">${plural(items.length, 'файл', 'файла', 'файлов')} · ${fmtSize(total)}</span>${peek}` +
      (folded ? '' : `<button class="link" data-day="${k}">${groupSelectLabel(k)}</button>`) +
      `</div>`;
    if (folded) { html += '</section>'; continue; }
    if (o.lazy) {
      // блоки по целому числу рядов: заглушка той же высоты, ряды не рвутся на стыке блоков
      const per = Math.max(1, Math.ceil(LAZY_TILES / S.cols)) * S.cols;
      for (let from = 0; from < items.length; from += per) {
        const ch = { items, from, to: Math.min(items.length, from + per), o };
        const n = o.lazy.chunks.push(ch) - 1;
        for (let i = from; i < ch.to; i++) o.lazy.byId.set(items[i].id, n);
        html += `<div class="tiles lazy" data-chunk="${n}" style="height:${chunkHeight(ch)}"></div>`;
      }
    } else {
      html += `<div class="tiles">${tilesHTML(items, 0, items.length, o)}</div>`;
    }
    html += '</section>';
  }
  return html;
}

function tilesHTML(items, from, to, o) {
  let html = '';
  for (let i = from; i < to; i++) {
    const it = items[i];
    html += tileHTML(it, {
      prevSer: i > 0 && it.s && items[i - 1].s === it.s,
      nextSer: i < items.length - 1 && it.s && items[i + 1].s === it.s,
      stack: o.collapse && it.s,
      noSeries: o.noSeries,
    });
  }
  return html;
}

// ============================================================ ленивая лента
// В папке бывают десятки тысяч снимков. В DOM держим только блоки плиток рядом с экраном,
// остальные — пустые заглушки точной высоты (в ряду ровно S.cols плиток, см. layoutTiles).
const LAZY_TILES = 150;
const LAZY_MARGIN = 1500;  // px над и под экраном, где блоки уже заполнены

function chunkHeight(ch) {
  const rows = Math.ceil((ch.to - ch.from) / S.cols);
  return `calc(${rows} * var(--thumb) + ${rows - 1} * var(--gap, 4px) + 2px)`;
}

/** Начать ленивую отрисовку view: daysHTML заполнит chunks, attachLazy подключит прокрутку. */
function newLazy(view) {
  view.lazy = { chunks: [], byId: new Map() };
  return view.lazy;
}

function attachLazy(view) {
  view.lazyObs?.disconnect();
  if (!view.lazyObs) {
    view.lazyObs = new IntersectionObserver(entries => {
      for (const en of entries) (en.isIntersecting ? fillChunk : emptyChunk)(view, en.target);
    }, { root: view, rootMargin: `${LAZY_MARGIN}px 0px` });
  }
  fillVisible(view);
  for (const box of $$('.tiles[data-chunk]', view)) view.lazyObs.observe(box);
}

function fillChunk(view, box) {
  if (!box.classList.contains('lazy')) return;
  const ch = view.lazy?.chunks[box.dataset.chunk];
  if (!ch) return;
  box.innerHTML = tilesHTML(ch.items, ch.from, ch.to, ch.o);
  box.classList.remove('lazy');
  box.style.height = '';
}

function emptyChunk(view, box) {
  const ch = view.lazy?.chunks[box.dataset.chunk];
  if (!ch || box.classList.contains('lazy') || box.contains(document.activeElement)) return;
  box.style.height = chunkHeight(ch);
  box.classList.add('lazy');
  box.textContent = '';
}

/** Сразу заполнить блоки у экрана — иначе после перерисовки на кадр мелькнёт пустая лента. */
function fillVisible(view) {
  if (!view.lazy) return;
  const r = view.getBoundingClientRect();
  for (const box of $$('.tiles.lazy', view)) {
    const b = box.getBoundingClientRect();
    if (b.bottom >= r.top - LAZY_MARGIN && b.top <= r.bottom + LAZY_MARGIN) fillChunk(view, box);
  }
}

/** Гарантировать, что плитка (и соседние блоки — для перехода по рядам) есть в DOM. */
function ensureTile(view, id) {
  const n = view.lazy?.byId.get(id);
  if (n == null) return;
  for (const k of [n - 1, n, n + 1]) {
    const box = $(`.tiles[data-chunk="${k}"]`, view);
    if (box) fillChunk(view, box);
  }
}

function emptyHTML(title, text) {
  return `<div class="empty-msg"><b>${title}</b>${text || ''}</div>`;
}

function gridList() {
  let list = S.items.filter(it => passFilters(it));
  // при поиске — от самых подходящих к менее подходящим
  list = S.search ? list.sort((a, b) => S.search.rank.get(a.id) - S.search.rank.get(b.id)) : sortList(list);
  if (S.collapse) {
    const seen = new Set();
    const out = [];
    for (const it of list) {
      if (!it.s) { out.push(it); continue; }
      if (seen.has(it.s)) continue;
      seen.add(it.s);
      out.push(seriesRep(it.s));
    }
    list = out;
  }
  return list;
}

function renderGrid() {
  const el = $('#view-grid');
  const full = gridList();
  S.list = full.filter(it => !S.folded.has(groupKey(it)));  // свёрнутые дни не участвуют в навигации
  if (!S.items.length) {
    el.innerHTML = emptyHTML(wasScanning ? 'Идёт сканирование…' : 'Здесь пока пусто', 'Фото и видео появятся по мере анализа.');
    return;
  }
  el.innerHTML = full.length ? daysHTML(full, { collapse: S.collapse, foldable: true, lazy: newLazy(el) })
    : emptyHTML('Ничего не найдено', 'Измените фильтры слева.');
  attachLazy(el);
}

function setFolded(keys, on) {
  for (const k of keys) { if (on) S.folded.add(k); else S.folded.delete(k); }
  // в настройках храним только последние 2000 ключей, чтобы список не рос бесконечно
  prefs.set('folded', [...S.folded].slice(-2000));
  if (S.focus && S.folded.has(groupKey(S.byId.get(S.focus) || {}))) S.focus = null;
  rerenderKeepScroll();
}

function renderSeries() {
  const el = $('#view-series');
  const order = seriesOrder();
  const frames = order.reduce((n, sid) => n + S.series.get(sid).length, 0);
  const done = order.filter(seriesDone).length;
  const list = S.onlyOpen ? order.filter(sid => !seriesDone(sid)) : order;
  let html = `<div class="list-head"><h2>Серии похожих кадров</h2>
    <span class="sub">${plural(order.length, 'серия', 'серии', 'серий')} · ${plural(frames, 'кадр', 'кадра', 'кадров')} · разобрано ${done}</span>
    <div class="spacer"></div>
    <label class="row check"><input type="checkbox" id="onlyOpen" ${S.onlyOpen ? 'checked' : ''}> только неразобранные</label>
    <button class="btn primary" data-act="start-review" ${order.length - done ? '' : 'disabled'}>Начать разбор ▶</button></div>`;
  if (!order.length) html += emptyHTML('Серий не найдено', 'Серия — это несколько похожих кадров, снятых подряд. Можно ослабить порог в «Поиске серий» слева.');
  else if (!list.length) html += emptyHTML('Все серии разобраны 🎉', 'Снимите галочку «только неразобранные», чтобы увидеть их.');
  for (const sid of list) {
    const arr = S.series.get(sid);
    const span = arr[arr.length - 1].t - arr[0].t;
    const best = arr.filter(i => i.sb).length;
    const isDone = seriesDone(sid);
    const kept = arr.filter(i => i.st === 1).length;
    html += `<div class="card scard ${isDone ? 'done' : ''}" data-sid="${sid}" style="--sc:${seriesColor(sid)}">
      <div class="card-head"><span class="dot" style="width:10px;height:10px;border-radius:3px;background:var(--sc)"></span>
        <span class="t">${fmtDT(arr[0].t)}</span>
        <span class="sub">${plural(arr.length, 'кадр', 'кадра', 'кадров')} за ${span < 60 ? span.toFixed(span < 10 ? 1 : 0) + ' с' : Math.round(span / 60) + ' мин'}${arr[0].cam ? ' · ' + esc(arr[0].cam) : ''}</span>
        <div class="spacer"></div>
        ${isDone ? `<span class="pill ok">✓ разобрано · оставлено ${kept}</span>` : `<span class="pill">★ рекомендуется оставить ${best}</span>`}
      </div>
      <div class="strip">${arr.slice(0, 40).map(it => tileHTML(it, { noSeries: true }).replace('<div class="badges">', `<div class="badges">${it.sb ? '<span class="bdg best">★</span>' : ''}`)).join('')}</div>
    </div>`;
  }
  el.innerHTML = html;
}

function dupGroups() {
  const out = [];
  for (const [gid, arr] of S.dups) {
    const vis = arr.filter(it => passFilters(it, false));
    if (vis.length) out.push([gid, arr]);
  }
  return out.sort((a, b) => (a[1][0].t ?? 0) - (b[1][0].t ?? 0));
}

function renderDups() {
  const el = $('#view-dups');
  const groups = dupGroups();
  const extra = groups.flatMap(([, arr]) => arr.filter(i => !i.db));
  const pending = extra.filter(i => i.st !== 2);
  let html = `<div class="list-head"><h2>Дубликаты</h2>
    <span class="sub">${plural(groups.length, 'группа', 'группы', 'групп')} · лишних копий ${extra.length} (${fmtSize(extra.reduce((s, i) => s + i.sz, 0))})</span>
    <div class="spacer"></div>
    <button class="btn primary" data-act="dup-all" ${pending.length ? '' : 'disabled'}>Во всех группах оставить ★ лучшее</button></div>`;
  if (!groups.length) html += emptyHTML('Дубликатов не найдено', 'Точные копии ищутся по содержимому файла, похожие — по картинке (например, пересжатые мессенджером).');
  for (const [gid, arr] of groups) {
    const exact = arr.every(i => i.dx);
    html += `<div class="card" data-gid="${gid}">
      <div class="card-head"><span class="pill ${exact ? 'exact' : ''}">${exact ? 'Точные копии' : 'Похожие'}</span>
        <span class="t">${plural(arr.length, 'файл', 'файла', 'файлов')}</span><span class="sub">${fmtDT(arr[0].t)}</span>
        <div class="spacer"></div>
        <button class="btn small" data-act="dup-auto" data-gid="${gid}">Оставить ★, остальные удалить</button></div>
      <div class="dup-items">${arr.map(it => `<div class="dup-item">${tileHTML(it, { noSeries: true }).replace('<div class="badges">', `<div class="badges">${it.db ? '<span class="bdg best">★ лучшее</span>' : ''}`)}
        <div class="meta"><b>${it.w && it.h ? `${it.w}×${it.h}` : '—'}</b> · ${fmtSize(it.sz)}<br>${esc(folderName(it))}/<b>${esc(fileName(it))}</b><br>${fmtDT(it.t)}${it.cam ? ' · ' + esc(it.cam) : ''}</div>
        <div class="acts"><button class="btn small keep" data-act="keep" data-id="${it.id}">✓ Оставить</button><button class="btn small reject" data-act="reject" data-id="${it.id}">✕ Удалить</button></div>
      </div>`).join('')}</div></div>`;
  }
  el.innerHTML = html;
}

function renderTrash() {
  const el = $('#view-trash');
  const all = sortList(S.items.filter(i => i.st === 2));
  const checked = all.some(i => i.ph != null);
  if (!checked) S.phoneFilter = 'all';
  S.list = S.phoneFilter === 'found' ? all.filter(i => typeof i.ph === 'string')
    : S.phoneFilter === 'missing' ? all.filter(i => i.ph === 0)
      : S.phoneFilter === 'mismatch' ? all.filter(i => i.ph === -1)
        : S.phoneFilter === 'unreadable' ? all.filter(i => i.ph === -2) : all;
  const size = all.reduce((s, i) => s + i.sz, 0);
  const where = S.trashTarget === 'phone'
    ? `Файлы будут <b>удалены с телефона</b>. Через провод они не попадают в «Недавно удалённые», поэтому снимки, которых нет в облаке, перед удалением копируются в <b>Корзину Windows</b>. Снимки, удалённые или сохранённые в облаке, удаляются без копии.`
    : (S.trashTarget === 'recycle'
      ? 'Файлы будут перемещены в <b>Корзину Windows</b> — при необходимости их можно восстановить оттуда.'
      : 'На этом диске нет Корзины Windows (флешка, карта памяти или сетевой диск) — файлы будут перенесены в папку <b>_rejected</b> внутри открытой папки. Удалите её вручную, когда будете уверены.')
    + (S.trashTarget !== 'phone' ? ` Отпечатки удалённых снимков запоминаются в журнале (сейчас в нём ${S.journal}): при сверке с телефоном их копии будут сразу помечены к удалению.` : '');
  let html = `<div class="list-head"><h2>Корзина отбора</h2>
    <span class="sub">${plural(all.length, 'файл', 'файла', 'файлов')} · ${fmtSize(size)}</span><div class="spacer"></div>
    ${S.source?.kind === 'folder' ? `<button class="btn" data-act="phone-find" ${all.length && !findRunning() ? '' : 'disabled'} title="Найти эти снимки на подключённом iPhone, чтобы удалить их и оттуда">${PHONE_ICON} Найти на телефоне</button>` : ''}
    <button class="btn ghost" data-act="trash-restore" ${all.length ? '' : 'disabled'}>Вернуть все</button>
    <button class="btn danger" data-act="trash-empty" ${all.length ? '' : 'disabled'}>Удалить ${all.length ? plural(all.length, 'файл', 'файла', 'файлов') : ''}${S.trashTarget === 'phone' ? ' с телефона' : ''}…</button></div>`;
  html += statsCardHTML();
  if (!S.stats) loadStats();
  const phoneSeg = checked ? `<div class="seg ph-seg">${[
    ['all', 'Все', all.length],
    ['found', 'Есть на телефоне', all.filter(i => typeof i.ph === 'string').length],
    ['missing', 'Не найдены', all.filter(i => i.ph === 0).length],
    ...(all.some(i => i.ph === -1) ? [['mismatch', 'Не совпало', all.filter(i => i.ph === -1).length]] : []),
    ...(all.some(i => i.ph === -2) ? [['unreadable', 'Не прочитано', all.filter(i => i.ph === -2).length]] : []),
  ].map(([v, l, n]) => `<button data-act="ph-filter" data-v="${v}" class="${S.phoneFilter === v ? 'active' : ''}">${l} <span class="cnt">${n}</span></button>`).join('')}</div>` : '';
  if (!all.length) {
    html += emptyHTML('Корзина отбора пуста', 'Отмечайте ненужные кадры клавишей <kbd>X</kbd> — они соберутся здесь. Удалятся только после подтверждения.');
  } else {
    html += `<div class="trash-bar">${where} Клавиша <kbd>U</kbd> или <kbd>P</kbd> — вернуть выбранные.</div>` + findBarHTML() + phoneSeg +
      (!S.list.length ? emptyHTML('Таких файлов нет')
        : S.phoneFilter === 'found' ? pairsHTML(S.list) : daysHTML(S.list, { noSeries: true, lazy: newLazy(el) }));
  }
  el.innerHTML = html;
  attachLazy(el);
}

// ============================================================ карта
let MAP = null, CLUSTER = null, mapStripList = [];

function renderMap() {
  if (!window.L) {
    $('#map').innerHTML = '<div class="map-off">Не удалось загрузить карту.<br>Для карты нужен интернет (подложка OpenStreetMap).</div>';
    return;
  }
  if (!MAP) {
    MAP = L.map('map', { worldCopyJump: true, zoomControl: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(MAP);
    MAP.on('moveend', updateMapStrip);
    MAP.setView([55.75, 37.62], 4);
  }
  setTimeout(() => MAP.invalidateSize(), 0);
  if (mapDirty) rebuildMarkers();
  else updateMapStrip();
}

function mapItems() { return S.items.filter(i => i.lat != null && passFilters(i)); }

function rebuildMarkers() {
  mapDirty = false;
  if (CLUSTER) MAP.removeLayer(CLUSTER);
  CLUSTER = L.markerClusterGroup({
    chunkedLoading: true, showCoverageOnHover: false, maxClusterRadius: 64, spiderfyOnMaxZoom: true,
    iconCreateFunction: c => {
      const it = S.byId.get(c.getAllChildMarkers()[0].options.itemId);
      return L.divIcon({
        html: `<div class="pm-cluster"><div class="pm-icon"><img src="${thumbUrl(it)}"></div><span>${c.getChildCount()}</span></div>`,
        className: '', iconSize: [52, 52],
      });
    },
  });
  const items = mapItems();
  CLUSTER.addLayers(items.map(it => {
    const m = L.marker([it.lat, it.lon], {
      itemId: it.id,
      icon: L.divIcon({ html: `<div class="pm-icon" style="width:44px;height:44px"><img src="${thumbUrl(it)}"></div>`, className: '', iconSize: [44, 44] }),
    });
    m.on('click', () => openViewer(mapStripList.some(x => x.id === it.id) ? mapStripList : [it], it.id));
    return m;
  }));
  MAP.addLayer(CLUSTER);
  if (S.mapTarget) {
    MAP.setView(S.mapTarget, 16);
    S.mapTarget = null;
  } else if (items.length) {
    MAP.fitBounds(L.latLngBounds(items.map(i => [i.lat, i.lon])).pad(0.15), { maxZoom: 14 });
  }
  updateMapStrip();
}

function updateMapStrip() {
  if (!MAP || S.view !== 'map') return;
  const b = MAP.getBounds();
  mapStripList = sortList(mapItems().filter(i => b.contains([i.lat, i.lon])));
  const total = S.items.filter(i => passFilters(i)).length;
  const noGps = total - mapItems().length;
  $('#mapCount').innerHTML = `В видимой области: <b>${mapStripList.length}</b>` + (noGps ? `<br><span class="muted">Без координат: ${noGps}</span>` : '');
  $('#mapStrip').innerHTML = mapStripList.slice(0, 400).map(it => tileHTML(it, { noSeries: true })).join('');
}

// ============================================================ выделение и статусы
// Плитки, которые сейчас в DOM (лента ленивая — остальные нарисуются уже с актуальными отметками)
const renderedTiles = () => $$('.tile[data-id], .ft[data-id]');

function setFocus(id, scroll = true) {
  $$('.tile.focus').forEach(el => el.classList.remove('focus'));
  S.focus = id;
  if (id == null) return;
  const view = $('#view-' + S.view);
  if (view) ensureTile(view, id);
  const el = view && $(`.tile[data-id="${id}"]`, view);
  if (el) {
    el.classList.add('focus');
    if (scroll) el.scrollIntoView({ block: 'nearest' });
  }
}

function refreshSelection() {
  for (const el of renderedTiles()) el.classList.toggle('sel', S.selected.has(+el.dataset.id));
  updateSelInfo();
}

function refreshTiles(ids) {
  ids = new Set(ids);
  for (const el of renderedTiles()) {
    const it = ids.has(+el.dataset.id) && S.byId.get(+el.dataset.id);
    if (!it) continue;
    el.classList.remove('st-0', 'st-1', 'st-2');
    el.classList.add('st-' + it.st);
  }
}

/** Применить статусы {id: status} одним шагом отмены. */
async function applyStatuses(changes, { record = true, rerender = true } = {}) {
  changes = new Map([...changes].filter(([id]) => S.byId.has(id)));
  if (!changes.size) return;
  if (record) {
    S.undo.push([...changes.keys()].map(id => [id, S.byId.get(id).st]));
    if (S.undo.length > 300) S.undo.shift();
  }
  const byStatus = new Map();
  for (const [id, st] of changes) {
    S.byId.get(id).st = st;
    (byStatus.get(st) || byStatus.set(st, []).get(st)).push(id);
  }
  refreshTiles(changes.keys());
  updateCounts();
  if (rerender && ['series', 'dups', 'trash', 'clean'].includes(S.view)) rerenderKeepScroll();
  try {
    for (const [st, ids] of byStatus) await api('/api/status', { ids, status: st });
  } catch (e) {
    toast('Не удалось сохранить: ' + esc(e.message), true);
  }
}

function rerenderKeepScroll() {
  const el = $('#view-' + S.view);
  const top = el.scrollTop;
  render();
  el.scrollTop = top;
  fillVisible(el);
  if (S.focus) setFocus(S.focus, false);
  refreshSelection();
}

const mark = (ids, st) => applyStatuses(new Map(ids.map(id => [id, st])));

async function undo() {
  const last = S.undo.pop();
  if (!last) { toast('Нечего отменять'); return; }
  await applyStatuses(new Map(last), { record: false });
  toast(`Отменено (${plural(last.length, 'файл', 'файла', 'файлов')})`);
}

function targetIds() {
  if (S.selected.size) return [...S.selected];
  return S.focus != null ? [S.focus] : [];
}

function selectDay(key) {
  S.selected = new Set(S.list.filter(i => groupKey(i) === key).map(i => i.id));
  refreshSelection();
}

// ============================================================ клавиатура и мышь в ленте
function gridClick(e, id) {
  if (e.ctrlKey || e.metaKey) {
    if (S.selected.has(id)) S.selected.delete(id); else S.selected.add(id);
    S.anchor = id;
  } else if (e.shiftKey && S.anchor != null) {
    const a = S.list.findIndex(i => i.id === S.anchor), b = S.list.findIndex(i => i.id === id);
    if (a >= 0 && b >= 0) {
      const [lo, hi] = a < b ? [a, b] : [b, a];
      S.selected = new Set(S.list.slice(lo, hi + 1).map(i => i.id));
    }
  } else {
    S.selected = new Set([id]);
    S.anchor = id;
  }
  setFocus(id, false);
  refreshSelection();
}

function moveFocus(dir, extend) {
  const list = S.list;
  if (!list.length) return;
  let idx = list.findIndex(i => i.id === S.focus);
  if (idx < 0) idx = 0;
  else if (dir === 'left') idx = Math.max(0, idx - 1);
  else if (dir === 'right') idx = Math.min(list.length - 1, idx + 1);
  else if (dir === 'home') idx = 0;
  else if (dir === 'end') idx = list.length - 1;
  else {
    const view = $('#view-' + S.view);
    ensureTile(view, S.focus);
    const cur = $(`.tile[data-id="${S.focus}"]`, view);
    if (!cur) return;
    const els = $$('.tiles > .tile', view);
    const r = cur.getBoundingClientRect();
    const step = dir === 'down' ? 1 : -1;
    let best = null, rowTop = null;
    for (let j = els.indexOf(cur) + step; j >= 0 && j < els.length; j += step) {
      const rr = els[j].getBoundingClientRect();
      if (Math.abs(rr.top - r.top) < 4) continue;
      if (rowTop === null) rowTop = rr.top;
      if (Math.abs(rr.top - rowTop) > 4) break;
      if (!best || Math.abs(rr.left - r.left) < Math.abs(best.r.left - r.left)) best = { el: els[j], r: rr };
    }
    if (!best) return;
    idx = list.findIndex(i => i.id === +best.el.dataset.id);
  }
  const id = list[idx].id;
  if (extend) {
    if (S.anchor == null) S.anchor = S.focus ?? id;
    const a = list.findIndex(i => i.id === S.anchor);
    const [lo, hi] = a < idx ? [a, idx] : [idx, a];
    S.selected = new Set(list.slice(lo, hi + 1).map(i => i.id));
  } else {
    S.selected = new Set([id]);
    S.anchor = id;
  }
  setFocus(id);
  refreshSelection();
}

function markAndAdvance(st) {
  const ids = targetIds();
  if (!ids.length) return;
  // следующий запоминаем до отметки: на странице «Можно удалить» отмеченный снимок сразу пропадает из списка
  const idx = ids.length === 1 ? S.list.findIndex(i => i.id === ids[0]) : -1;
  const next = idx >= 0 ? S.list[idx + 1] : null;
  mark(ids, st);
  if (!next) return;
  if (S.list.some(i => i.id === ids[0])) { moveFocus('right'); return; }
  if (S.list.some(i => i.id === next.id)) {
    S.selected = new Set([next.id]);
    S.anchor = next.id;
    setFocus(next.id);
    refreshSelection();
  }
}

function gridKey(e) {
  const c = e.code;
  if ((e.ctrlKey || e.metaKey) && c === 'KeyZ') { e.preventDefault(); undo(); return; }
  if (!['grid', 'trash', 'clean'].includes(S.view)) return;
  if ((e.ctrlKey || e.metaKey) && c === 'KeyA') {
    e.preventDefault();
    S.selected = new Set(S.list.map(i => i.id));
    refreshSelection();
    return;
  }
  const arrows = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down', Home: 'home', End: 'end' };
  if (arrows[e.key]) { e.preventDefault(); moveFocus(arrows[e.key], e.shiftKey); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const it = S.byId.get(S.focus);
  switch (c) {
    case 'Enter': case 'Space':
      e.preventDefault();
      if (it) (S.collapse && it.s && S.view === 'grid' ? openSeries(it.s) : openViewer(S.list, it.id));
      break;
    case 'KeyX': case 'Delete': case 'Backspace': e.preventDefault(); markAndAdvance(2); break;
    case 'KeyP': markAndAdvance(1); break;
    case 'KeyU': markAndAdvance(0); break;
    case 'KeyS': if (it?.s) openSeries(it.s); break;
    case 'KeyF': if (it) findSimilar(it); break;
    case 'KeyC': {
      const ids = [...S.selected];
      if (ids.length >= 2 && ids.length <= 9) openCompare(ids);
      else toast('Выберите от 2 до 9 файлов (Ctrl+клик), затем нажмите C');
      break;
    }
    case 'Escape': S.selected.clear(); refreshSelection(); break;
    default: break;
  }
}

// ============================================================ просмотрщик
const V = { list: [], idx: 0, info: false, zoom: false, strip: [0, 0], stripList: null };

function openViewer(list, id) {
  V.list = list.slice();
  V.idx = Math.max(0, V.list.findIndex(i => i.id === id));
  V.zoom = false;
  V.stripList = null;
  $('#viewer').classList.remove('hidden');
  showV();
}

function closeViewer() {
  const v = $('#vMedia video');
  if (v) v.pause();
  $('#vMedia').innerHTML = '';
  $('#viewer').classList.add('hidden');
  const it = V.list[V.idx];
  if (it && $('#review').classList.contains('hidden') && ['grid', 'trash'].includes(S.view)) {
    S.selected = new Set([it.id]);
    S.anchor = it.id;
    setFocus(it.id);
    refreshSelection();
  }
}

function progressiveImg(img, it, onFull) {
  img.src = thumbUrl(it);
  if (it.k === 'v') return;
  const full = new Image();
  full.onload = () => { if (img.dataset.id === String(it.id)) { img.src = full.src; onFull?.(); } };
  full.onerror = () => { if (img.dataset.id === String(it.id)) img.title = 'Не удалось открыть оригинал — показана миниатюра'; };
  img.dataset.id = it.id;
  full.src = fileUrl(it);
}

function showV() {
  const it = V.list[V.idx];
  if (!it) { closeViewer(); return; }
  V.zoom = false;
  $('#vName').textContent = fileName(it);
  const meta = [fmtDT(it.t), it.w && it.h ? `${it.w}×${it.h}` : '', fmtSize(it.sz), it.cam, CATS[it.c]?.label].filter(Boolean);
  $('#vMeta').textContent = meta.join(' · ');
  $('#vPos').textContent = `${V.idx + 1} / ${V.list.length}`;
  $('#vSeriesBtn').classList.toggle('hidden', !it.s || !$('#review').classList.contains('hidden'));
  const box = $('#vMedia');
  box.classList.remove('zoomed');
  const old = $('video', box);
  if (old) old.pause();
  if (it.k === 'v') {
    if (PLAYABLE.has(it.e)) {
      box.innerHTML = `<video src="${fileUrl(it)}" poster="${thumbUrl(it)}" controls autoplay playsinline></video>`;
      $('video', box).addEventListener('error', () => videoFallback(it, 'Браузер не умеет воспроизводить этот кодек.'));
    } else {
      videoFallback(it, `Формат ${it.e} не воспроизводится в браузере.`);
    }
  } else {
    box.innerHTML = '<img alt="" style="width:100%;height:100%">';
    progressiveImg($('img', box), it);
    for (const d of [1, -1]) {
      const n = V.list[V.idx + d];
      if (n && n.k === 'i') new Image().src = fileUrl(n);
    }
  }
  updateVBadge();
  renderStrip();
  if (V.info) loadInfo(it);
  if (['grid', 'trash'].includes(S.view) && $('#review').classList.contains('hidden')) setFocus(it.id);
}

/** Лента миниатюр внизу просмотрщика: окно ±STRIP кадров вокруг текущего. */
const STRIP = 60;
function renderStrip() {
  const el = $('#vStrip');
  const n = V.list.length;
  const [from, to] = V.strip;
  const fits = V.stripList === V.list && ((V.idx >= from + 15 && V.idx < to - 15) || (from === 0 && to === n));
  if (!fits) {
    const a = Math.max(0, V.idx - STRIP), b = Math.min(n, V.idx + STRIP + 1);
    V.strip = [a, b];
    V.stripList = V.list;
    let html = '', prevDay = null;
    for (let i = a; i < b; i++) {
      const it = V.list[i];
      const day = dayKey(it.t);
      const sameSer = it.s && V.list[i + 1]?.s === it.s;
      html += `<div class="ft st-${it.st}${prevDay !== null && day !== prevDay ? ' newday' : ''}${it.s ? ' ser' : ''}${sameSer ? ' ser-next' : ''}" data-id="${it.id}" data-i="${i}"` +
        `${it.s ? ` style="--sc:${seriesColor(it.s)}"` : ''} title="${esc(fileName(it))} · ${fmtDT(it.t)}">` +
        `<img loading="lazy" src="${thumbUrl(it)}" alt="">${it.k === 'v' ? '<span class="ft-v">▶</span>' : ''}</div>`;
      prevDay = day;
    }
    el.innerHTML = html;
  }
  $$('.ft.cur', el).forEach(x => x.classList.remove('cur'));
  const cur = $(`.ft[data-i="${V.idx}"]`, el);
  if (cur) {
    cur.classList.add('cur');
    const left = cur.offsetLeft - el.clientWidth / 2 + cur.offsetWidth / 2;
    el.scrollTo({ left, behavior: fits ? 'smooth' : 'instant' });
  }
}

function videoFallback(it, why) {
  $('#vMedia').innerHTML = `<div class="v-fallback"><img src="${thumbUrl(it)}" alt=""><div>${why}</div>
    <p><button class="btn" data-act="external">Открыть в системном плеере</button></p></div>`;
}

function updateVBadge() {
  const it = V.list[V.idx];
  $('#vBadge').innerHTML = it?.st === 1 ? '<span class="k">✓ Оставить</span>' : it?.st === 2 ? '<span class="r">✕ К удалению</span>' : '';
}

function vStep(d) {
  const n = V.idx + d;
  if (n < 0 || n >= V.list.length) return;
  V.idx = n;
  showV();
}

function vMark(st) {
  const it = V.list[V.idx];
  if (!it) return;
  mark([it.id], st); // статус меняется локально сразу, сохранение идёт в фоне
  flashMark(st);
  if (st !== 0 && V.idx < V.list.length - 1) vStep(1);
  else updateVBadge();
}

function flashMark(st) {
  const f = $('#vFlash');
  f.className = 'v-flash';
  if (st === 0) return;
  f.textContent = st === 1 ? '✓' : '✕';
  void f.offsetWidth; // перезапуск анимации
  f.className = 'v-flash ' + (st === 1 ? 'k' : 'r');
}

function vZoom(ev) {
  const box = $('#vMedia');
  const img = $('img', box);
  if (!img) return;
  if (V.zoom) {
    V.zoom = false;
    box.classList.remove('zoomed');
    img.style.width = img.style.height = '100%';
    return;
  }
  const r = img.getBoundingClientRect();
  const nw = img.naturalWidth, nh = img.naturalHeight;
  const sc = Math.min(r.width / nw, r.height / nh);
  const x0 = r.left + (r.width - nw * sc) / 2, y0 = r.top + (r.height - nh * sc) / 2;
  const rx = ev ? Math.min(1, Math.max(0, (ev.clientX - x0) / (nw * sc))) : 0.5;
  const ry = ev ? Math.min(1, Math.max(0, (ev.clientY - y0) / (nh * sc))) : 0.5;
  V.zoom = true;
  box.classList.add('zoomed');
  img.style.width = nw + 'px';
  img.style.height = nh + 'px';
  box.scrollLeft = rx * nw - box.clientWidth / 2;
  box.scrollTop = ry * nh - box.clientHeight / 2;
}

function qualityText(q) {
  const parts = [];
  // шкала SigLIP неравномерная (у половины снимков меньше 0.07), поэтому словами, а не числом
  if (q.aes != null) parts.push(`красота кадра: ${q.aes >= AES_BEST ? 'высокая, один из лучших' : q.aes >= 0.07 ? 'средняя' : 'низкая'}`);
  if (q.faces) {
    parts.push(`${plural(q.faces, 'лицо', 'лица', 'лиц')}${q.closed ? `, <span style="color:var(--reject)">закрыты глаза у ${q.closed}</span>` : ', глаза открыты'}`);
  } else if (q.faces === 0) parts.push('лиц нет');
  return parts.join('<br>') || '—';
}

async function loadInfo(it) {
  const el = $('#vInfo');
  el.classList.remove('hidden');
  let d;
  try { d = await api('/api/item/' + it.id); } catch (e) { el.innerHTML = esc(e.message); return; }
  if (V.list[V.idx]?.id !== it.id) return;
  const rows = [
    ['Файл', esc(fileName(it))],
    ['Папка', esc(folderName(it))],
    ['Дата', `${fmtDT(d.ts)}${d.ts ? `<br><span class="muted">${TS_SOURCE[d.ts_source] || ''}</span>` : ''}`],
    ['Размер', fmtSize(it.sz)],
    ['Разрешение', d.width && d.height ? `${d.width}×${d.height} (${((d.width * d.height) / 1e6).toFixed(1)} Мп)` : '—'],
    ['Камера', esc([d.make, d.model].filter(Boolean).join(' ')) || '—'],
    d.software ? ['Программа', esc(d.software)] : null,
    ['Категория', CATS[d.category]?.label || d.category],
    d.duration ? ['Длительность', fmtDur(d.duration)] : null,
    d.sharpness != null ? ['Резкость', Math.round(d.sharpness)] : null,
    d.quality ? ['Оценка кадра', qualityText(d.quality)] : null,
    ['Место', d.lat != null ? `${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}<br><button class="link" data-act="onmap">показать на карте</button>` : '—'],
    d.children?.length ? ['Связанные', d.children.map(c => esc(c.path.split('/').pop()) + ' · ' + fmtSize(c.size)).join('<br>')] : null,
    it.s ? ['Серия', `${S.series.get(it.s)?.length || ''} кадров${it.sb ? ' · ★ рекомендован' : ''}`] : null,
    it.m ? ['Сверка', `${MATCH[it.m]?.label || it.m}${it.ms === 0 ? ' (похоже)' : ''}${it.mr ? `<br><span class="muted">${esc(it.mr)}</span>` : ''}`] : null,
    it.d ? ['Дубликат', `${it.dx ? 'точная копия' : 'похожий'}${it.db ? ' · ★ лучший в группе' : ''}`] : null,
    d.error ? ['Ошибка', `<span style="color:var(--reject)">${esc(d.error)}</span>`] : null,
  ].filter(Boolean);
  el.innerHTML = `<dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    <div class="acts"><button class="btn" data-act="reveal">Показать в проводнике</button>
    <button class="btn" data-act="external">Открыть в программе по умолчанию</button></div>`;
}

function viewerKey(e) {
  const c = e.code;
  if ((e.ctrlKey || e.metaKey) && c === 'KeyZ') { e.preventDefault(); undo().then(updateVBadge); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const inVideo = e.target.tagName === 'VIDEO';
  switch (e.key) {
    case 'ArrowLeft': if (!inVideo) { e.preventDefault(); vStep(-1); } return;
    case 'ArrowRight': if (!inVideo) { e.preventDefault(); vStep(1); } return;
    case 'ArrowUp': if (!inVideo) { e.preventDefault(); vMark(1); } return;
    case 'ArrowDown': if (!inVideo) { e.preventDefault(); vMark(2); } return;
    case 'Home': V.idx = 0; showV(); return;
    case 'End': V.idx = V.list.length - 1; showV(); return;
    case 'Escape': if (V.zoom) vZoom(); else closeViewer(); return;
    default: break;
  }
  switch (c) {
    case 'KeyX': case 'Delete': case 'Backspace': e.preventDefault(); vMark(2); break;
    case 'KeyP': vMark(1); break;
    case 'KeyU': vMark(0); break;
    case 'KeyI': V.info = !V.info; $('#vInfo').classList.toggle('hidden', !V.info); if (V.info) loadInfo(V.list[V.idx]); break;
    case 'KeyZ': vZoom(); break;
    case 'KeyS': { const it = V.list[V.idx]; if (it?.s && $('#review').classList.contains('hidden')) { closeViewer(); openSeries(it.s); } break; }
    case 'KeyF': { const it = V.list[V.idx]; if (it) { closeViewer(); findSimilar(it); } break; }
    case 'Space': if (!$('#vMedia video')) { e.preventDefault(); vStep(1); } break;
    default: break;
  }
}

// ============================================================ разбор серии / сравнение
const R = { mode: 'series', sid: null, items: [], dec: new Map(), zoom: false, zf: 3, suggested: false };

function openSeries(sid) {
  const arr = S.series.get(sid);
  if (!arr) return;
  R.mode = 'series';
  R.sid = sid;
  R.items = arr.slice();
  const untouched = R.items.every(i => i.st === 0);
  R.suggested = untouched;
  R.dec = new Map(R.items.map(i => [i.id, untouched ? (i.sb ? 1 : 2) : (i.st || 1)]));
  showReview();
}

function openCompare(ids) {
  R.mode = 'compare';
  R.sid = null;
  R.items = ids.map(id => S.byId.get(id)).filter(Boolean).sort((a, b) => a.t - b.t);
  R.suggested = false;
  R.dec = new Map(R.items.map(i => [i.id, i.st || 1]));
  showReview();
}

function showReview() {
  const el = $('#review');
  el.classList.remove('hidden');
  const n = R.items.length;
  const first = R.items[0], last = R.items[n - 1];
  if (R.mode === 'series') {
    const order = seriesOrder();
    const pos = order.indexOf(R.sid);
    const left = order.filter(s => !seriesDone(s) && s !== R.sid).length;
    $('#rTitle').textContent = `Серия · ${plural(n, 'кадр', 'кадра', 'кадров')}`;
    $('#rSub').textContent = [fmtDT(first.t), `за ${(last.t - first.t).toFixed(1)} с`, first.cam,
      pos >= 0 ? `серия ${pos + 1} из ${order.length}, не разобрано ещё ${left}` : '',
      R.suggested ? (R.items.some(i => i.qf != null || i.qa != null)
        ? '★ отмечены лучшие по резкости, глазам и красоте кадра — проверьте и нажмите Enter'
        : '★ отмечены самые резкие — проверьте и нажмите Enter') : ''].filter(Boolean).join(' · ');
  } else {
    $('#rTitle').textContent = `Сравнение · ${plural(n, 'файл', 'файла', 'файлов')}`;
    $('#rSub').textContent = 'Клик по кадру — оставить или удалить';
  }
  $('#rSkip').classList.toggle('hidden', R.mode !== 'series');
  $('[data-act="suggest"]', el).classList.toggle('hidden', R.mode !== 'series');
  $('#rDone').innerHTML = R.mode === 'series' ? 'Готово, дальше <kbd>Enter</kbd>' : 'Сохранить <kbd>Enter</kbd>';

  const cols = n <= 3 ? n : n === 4 ? 2 : n <= 6 ? 3 : n <= 12 ? 4 : 5;
  const rows = Math.ceil(n / cols);
  const grid = $('#rGrid');
  grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  grid.style.gridTemplateRows = rows <= 3 ? `repeat(${rows}, minmax(0, 1fr))` : '';
  grid.style.gridAutoRows = rows <= 3 ? '' : '36vh';
  const maxSh = Math.max(...R.items.map(i => i.sh || 0), 1);
  const sharpest = R.items.reduce((a, b) => ((b.sh || 0) > (a.sh || 0) ? b : a));
  grid.innerHTML = R.items.map((it, i) => {
    const best = R.mode === 'series' ? it.sb : it.id === sharpest.id && it.k === 'i';
    return `<div class="rf" data-id="${it.id}">
      ${best ? `<span class="best-tag">★ ${R.mode === 'series' ? 'рекомендуется' : 'самый резкий'}</span>` : ''}
      ${it.qc > 0 ? `<span class="eyes-tag" title="Закрыты глаза: ${it.qc} из ${plural(it.qf, 'лица', 'лиц', 'лиц')}"><svg class="i"><use href="#i-eye-off"/></svg>закрыты глаза</span>` : ''}
      <div class="rf-img"><img alt=""></div>
      <div class="rf-bar"><span class="n">${i < 9 ? i + 1 : ''}</span><span>${R.mode === 'series' ? fmtTime(it.t) : esc(fileName(it))}</span>
        ${it.sh != null ? `<span class="sharp" title="Резкость: ${Math.round(it.sh)}"><i style="width:${Math.round((it.sh / maxSh) * 100)}%"></i></span>` : ''}
        <span class="muted">${it.w && it.h ? `${it.w}×${it.h}` : ''}</span><span class="state"></span></div></div>`;
  }).join('');
  $$('.rf', grid).forEach(f => progressiveImg($('img', f), S.byId.get(+f.dataset.id)));
  setZoom(R.zoom);
  refreshReview();
}

function refreshReview() {
  for (const f of $$('#rGrid .rf')) {
    const d = R.dec.get(+f.dataset.id);
    f.classList.toggle('k', d === 1);
    f.classList.toggle('r', d === 2);
    $('.state', f).textContent = d === 2 ? '✕ удалить' : '✓ оставить';
  }
}

function toggleFrame(id) {
  R.dec.set(id, R.dec.get(id) === 2 ? 1 : 2);
  R.suggested = false;
  refreshReview();
}

function setZoom(on) {
  R.zoom = on;
  $('#rGrid').classList.toggle('zoom', on);
  $('#review [data-act="zoom"]').classList.toggle('on', on);
  if (!on) $$('#rGrid img').forEach(img => { img.style.transform = ''; });
}

function closeReview() {
  setZoom(false);
  $('#review').classList.add('hidden');
  $('#rGrid').innerHTML = '';
}

function nextSeriesId(current) {
  const order = seriesOrder();
  const i = order.indexOf(current);
  const rot = [...order.slice(i + 1), ...order.slice(0, Math.max(0, i))];
  return rot.find(s => s !== current && !seriesDone(s)) ?? null;
}

async function reviewDone() {
  const changes = new Map();
  for (const [id, d] of R.dec) changes.set(id, d);
  await applyStatuses(changes, { rerender: false });
  if (R.mode === 'series') {
    const rej = [...R.dec.values()].filter(d => d === 2).length;
    const next = nextSeriesId(R.sid);
    if (next != null) {
      openSeries(next);
      toast(rej ? `К удалению: ${plural(rej, 'кадр', 'кадра', 'кадров')}` : 'Серия сохранена', false, 1600);
    } else {
      closeReview();
      toast('Все серии разобраны 🎉 Проверьте «Корзину отбора» и удалите лишнее.');
    }
  } else {
    closeReview();
  }
  rerenderKeepScroll();
}

function reviewSkip() {
  if (R.mode !== 'series') return;
  const order = seriesOrder();
  const i = order.indexOf(R.sid);
  const next = order[i + 1] ?? order[0];
  if (next != null && next !== R.sid) openSeries(next);
}

function reviewKey(e) {
  const c = e.code;
  if ((e.ctrlKey || e.metaKey) && c === 'KeyZ') { e.preventDefault(); undo(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'Escape') { if (R.zoom) setZoom(false); else { closeReview(); rerenderKeepScroll(); } return; }
  if (e.key === 'Enter') { e.preventDefault(); reviewDone(); return; }
  if (e.key === 'Tab') { e.preventDefault(); reviewSkip(); return; }
  if (/^Digit[1-9]$/.test(c) || /^Numpad[1-9]$/.test(c)) {
    const it = R.items[+c.slice(-1) - 1];
    if (it) toggleFrame(it.id);
    return;
  }
  if (c === 'KeyZ') setZoom(!R.zoom);
}

function applySuggestion() {
  R.dec = new Map(R.items.map(i => [i.id, i.sb ? 1 : 2]));
  R.suggested = true;
  refreshReview();
}

// ============================================================ действия
async function onAction(act, el) {
  const id = +el.dataset.id;
  switch (act) {
    // просмотрщик
    case 'prev': vStep(-1); break;
    case 'next': vStep(1); break;
    case 'close':
      if (el.closest('#viewer')) closeViewer(); else { closeReview(); rerenderKeepScroll(); }
      break;
    case 'keep': if (el.closest('#viewer')) vMark(1); else mark([id], 1); break;
    case 'reject': if (el.closest('#viewer')) vMark(2); else mark([id], 2); break;
    case 'info': V.info = !V.info; $('#vInfo').classList.toggle('hidden', !V.info); if (V.info) loadInfo(V.list[V.idx]); break;
    case 'series': { const it = V.list[V.idx]; if (it?.s) { closeViewer(); openSeries(it.s); } break; }
    case 'similar': { const it = V.list[V.idx]; closeViewer(); findSimilar(it); break; }
    case 'clean-mark': cleanMark(cleanSections().filter(x => !el.dataset.sec || x.key === el.dataset.sec)); break;
    case 'clean-more': if (S.cleanOpen.has(el.dataset.sec)) S.cleanOpen.delete(el.dataset.sec); else S.cleanOpen.add(el.dataset.sec); rerenderKeepScroll(); break;
    case 'go-ai': setView('ai'); break;
    case 'reveal': api('/api/reveal/' + V.list[V.idx].id, {}); break;
    case 'external': api('/api/open-external/' + V.list[V.idx].id, {}); break;
    case 'onmap': { const it = V.list[V.idx]; closeViewer(); S.mapTarget = [it.lat, it.lon]; mapDirty = true; setView('map'); break; }
    // разбор
    case 'suggest': applySuggestion(); break;
    case 'keepall': R.dec = new Map(R.items.map(i => [i.id, 1])); refreshReview(); break;
    case 'zoom': setZoom(!R.zoom); break;
    case 'skip': reviewSkip(); break;
    case 'done': reviewDone(); break;
    // списки
    case 'start-review': {
      const order = seriesOrder();
      const sid = order.find(s => !seriesDone(s));
      if (sid != null) openSeries(sid);
      break;
    }
    case 'dup-auto': {
      const arr = S.dups.get(+el.dataset.gid) || [];
      applyStatuses(new Map(arr.map(i => [i.id, i.db ? 1 : 2])));
      break;
    }
    case 'dup-all': {
      const groups = dupGroups();
      const changes = new Map();
      for (const [, arr] of groups) for (const i of arr) changes.set(i.id, i.db ? 1 : 2);
      const rej = [...changes.values()].filter(v => v === 2).length;
      if (await confirmModal('Оставить лучшие копии?', `<p>В каждой из ${plural(groups.length, 'группы', 'групп', 'групп')} будет оставлен файл, отмеченный ★ (больше разрешение, есть EXIF), а ${plural(rej, 'файл', 'файла', 'файлов')} — помечены к удалению.</p><p class="muted">Сами файлы пока не удаляются — их можно проверить в «Корзине отбора».</p>`, 'Пометить')) {
        applyStatuses(changes);
      }
      break;
    }
    case 'trash-restore': applyStatuses(new Map(S.items.filter(i => i.st === 2).map(i => [i.id, 0]))); break;
    case 'trash-empty': emptyTrash(); break;
    case 'phone-find': phoneFind(); break;
    case 'stats-more': statsModal(); break;
    case 'phone-find-all': phoneFind(true); break;
    case 'phone-find-wide': phoneFind(false, true); break;
    case 'fold': setFolded([el.dataset.key], !S.folded.has(el.dataset.key)); break;
    case 'fold-all': setView('grid'); setFolded(new Set(gridList().map(groupKey)), true); break;
    case 'unfold-all': setView('grid'); setFolded([...S.folded], false); break;
    case 'ai-install': aiCall('/api/ai/install', { model: el.dataset.model }); break;
    case 'ai-remove': {
      const m = el.dataset.model === 'faces' ? S.aiInfo?.faces : S.aiInfo;
      if (await confirmModal('Удалить модель?', `<p>С диска будут удалены файлы модели (${fmtSize(m?.size)}). Результаты уже проведённого анализа останутся.</p><p class="muted">Скачать модель снова можно на этой же странице.</p>`, 'Удалить', true)) aiCall('/api/ai/remove', { model: el.dataset.model });
      break;
    }
    case 'ai-rate': aiCall('/api/ai/rate'); break;
    case 'ai-rate-stop': aiCall('/api/ai/rate-stop'); break;
    case 'q-cat': S.aiSel = new Set([el.dataset.key]); S.search = null; $('#aiQuery').value = ''; S.view = 'grid'; filtersChanged(); break;
    case 'ai-analyze': aiCall('/api/ai/analyze'); break;
    case 'ai-stop': aiCall('/api/ai/stop'); break;
    case 'ai-cat': S.aiSel = new Set([el.dataset.key]); S.search = null; $('#aiQuery').value = ''; S.view = 'grid'; filtersChanged(); break;
    case 'fold-group': {
      const g = el.dataset.foldGroup;
      if (S.sbFolded.has(g)) S.sbFolded.delete(g); else S.sbFolded.add(g);
      prefs.set('sbFolded', [...S.sbFolded]);
      el.closest('.sb-group').classList.toggle('folded', S.sbFolded.has(g));
      break;
    }
    case 'ph-filter': S.phoneFilter = el.dataset.v; S.selected.clear(); S.focus = null; render(); break;
    default: if (act.startsWith('st-')) statsAction(act, el); break;
  }
}

async function emptyTrash() {
  const list = S.items.filter(i => i.st === 2);
  const size = list.reduce((s, i) => s + i.sz, 0);
  const pairs = list.filter(i => i.cx?.length).length;
  const phone = S.trashTarget === 'phone';
  const notInCloud = list.filter(i => !['cloud', 'deleted'].includes(i.m)).length;
  const onPhone = !phone && S.find.phase === 'done' ? list.filter(i => typeof i.ph === 'string').length : 0;
  const where = S.trashTarget === 'recycle' ? 'в <b>Корзину Windows</b>' : 'в папку <b>_rejected</b> (на этом диске нет Корзины Windows)';
  const ok = await confirmModal(phone ? 'Удалить с телефона?' : 'Удалить отобранные файлы?',
    (phone
      ? `<p>С телефона будут удалены ${plural(list.length, 'файл', 'файла', 'файлов')} (${fmtSize(size)}).</p>` +
        (notInCloud ? `<p>${plural(notInCloud, 'снимок', 'снимка', 'снимков')} нет в облаке — их копии сначала попадут в Корзину Windows.</p>` : '<p>Все они есть в облаке или удалены там, копии не сохраняются.</p>') +
        '<p class="muted">Не отключайте телефон и не блокируйте его до конца удаления.</p>'
      : `<p>${plural(list.length, 'файл', 'файла', 'файлов')} (${fmtSize(size)}) будут перемещены ${where}.</p>`) +
    (pairs ? `<p>Вместе с ними удалятся связанные файлы (RAW, Live Photo) — у ${plural(pairs, 'снимка', 'снимков', 'снимков')}.</p>` : '') +
    (onPhone ? `<p style="margin-top:12px"><b>Будут удалены с телефона</b> — слева файл из облака, справа миниатюра с iPhone:</p>
       <div class="mini-pairs">${list.filter(i => typeof i.ph === 'string').map(i => `
         <div class="mp" title="${esc(fileName(i))} ↔ ${esc(i.ph)}">
           <img src="${thumbUrl(i)}" alt=""><span>=</span><img src="${phoneThumbUrl(i)}" alt="" onerror="this.style.visibility='hidden'">
           <small>${esc(i.ph.split('/').pop())}</small></div>`).join('')}</div>
       <label class="row check" style="margin-top:12px"><input type="checkbox" id="delPhone" checked>
       <span>Удалить и с телефона «${esc(S.find.device || 'телефон')}» — ${plural(onPhone, 'найденный снимок', 'найденных снимка', 'найденных снимков')} вместе с Live-видео и правками</span></label>
       <p class="note">С телефона файлы удаляются безвозвратно (через провод нет «Недавно удалённых»), но их копии из облака останутся в Корзине Windows.</p>` : ''),
    'Удалить', true);
  if (!ok) return;
  const delPhone = !!(onPhone && $('#delPhone')?.checked);
  // удаление может идти минутами (с телефона каждый файл читается целиком для сверки) — сообщение висит до конца
  const busy = toast(`<span class="spin"></span> ${delPhone
    ? `Удаляю ${plural(onPhone, 'файл', 'файла', 'файлов')} с телефона — не отключайте и не блокируйте его…`
    : 'Удаляю…'}`, false, 24 * 3600 * 1000);
  try {
    const r = await api('/api/trash/empty', { phone: delPhone });
    S.undo = [];
    toast(`Удалено: ${plural(r.removed, 'файл', 'файла', 'файлов')}, освобождено ${fmtSize(r.freed)}` +
      (r.phone ? `<br>С телефона удалено: ${plural(r.phone.removed, 'файл', 'файла', 'файлов')}, освобождено ${fmtSize(r.phone.freed)}` : ''));
    S.stats = null;
    if (r.errors?.length) toast('Не удалось удалить:<br>' + r.errors.slice(0, 5).map(esc).join('<br>'), true, 10000);
    if (r.phone?.errors?.length) toast('С телефона не удалось удалить:<br>' + r.phone.errors.slice(0, 5).map(esc).join('<br>'), true, 12000);
    busy.remove();
    await loadItems();
  } catch (e) {
    toast(esc(e.message), true);
  } finally {
    busy.remove();
  }
}

async function phoneDialog() {
  let devs = [], refs = { roots: [], journal: 0 };
  toast('Ищу подключённые телефоны…', false, 1500);
  try {
    [devs, refs] = await Promise.all([api('/api/phones').then(r => r.devices), api('/api/refs')]);
  } catch (e) { toast(esc(e.message), true); return; }
  const roots = refs.roots.filter(r => r.exists);
  const html = (devs.length
    ? `<div class="dev-list">${devs.map((d, i) => `<label><input type="radio" name="dev" value="${i}" ${i ? '' : 'checked'}> ${esc(d.name)}</label>`).join('')}</div>`
    : '<p>Телефон не найден. Подключите iPhone кабелем, разблокируйте его и нажмите «Доверять этому компьютеру». Потом откройте это окно снова.</p>') +
    `<p><b>С чем сверять</b> — папки облака, которые уже открывались в программе:</p>` +
    (roots.length
      ? `<div class="ref-list">${roots.map(r => `<label><input type="checkbox" value="${esc(r.root)}" checked><span title="${esc(r.root)}">${esc(r.root)}</span><small>${r.count}</small></label>`).join('')}</div>`
      : '<p class="note">Пока нет ни одной. Сначала откройте папку Яндекс Диска с фото («Открыть папку…») и дождитесь окончания сканирования — иначе сверять не с чем.</p>') +
    `<p class="note">В журнале удалений: ${refs.journal}. Снимки, которые уже есть в облаке (тот же размер и дата), с телефона не копируются. Список файлов iPhone читается долго — примерно 25–30 минут на 30 тысяч файлов. Держите телефон разблокированным и подключённым.</p>`;
  if (!await confirmModal('Сверка телефона с облаком', html, devs.length ? 'Открыть телефон' : 'Закрыть') || !devs.length) return;
  const dev = devs[+($('#mBody input[name=dev]:checked')?.value || 0)];
  const sel = $$('#mBody .ref-list input:checked').map(x => x.value);
  try {
    const st = await api('/api/open-phone', { name: dev.name, path: dev.path, roots: sel.length === roots.length ? null : sel });
    resetForNewSource();
    applyState(st);
    setView('grid');
  } catch (e) { toast(esc(e.message), true); }
}

async function confirmCopy(sc) {
  const ok = await confirmModal('Скопировать файлы с телефона?',
    `<p>С облаком не совпало <b>${plural(sc.total, 'файл', 'файла', 'файлов')}</b> (${fmtSize(sc.copy_bytes)}). Чтобы показать их и найти серии, их нужно скопировать на компьютер.</p>
     <p>Если это неожиданно много — скорее всего, папка облака ещё не открыта в программе. Нажмите «Отмена», откройте папку Яндекс Диска с фото (например, <b>Фотокамера</b>), дождитесь сканирования и подключите телефон снова.</p>`,
    'Копировать');
  handleScan.asked = false;
  if (!ok) return;
  try { handleScan(await api('/api/phone/allow-copy', {})); } catch (e) { toast(esc(e.message), true); }
}

async function refsDialog() {
  let refs;
  try { refs = await api('/api/refs'); } catch (e) { toast(esc(e.message), true); return; }
  const roots = refs.roots.filter(r => r.exists);
  const html = roots.length
    ? `<div class="ref-list">${roots.map(r => `<label><input type="checkbox" value="${esc(r.root)}" ${r.selected ? 'checked' : ''}><span title="${esc(r.root)}">${esc(r.root)}</span><small>${r.count}</small></label>`).join('')}</div>
       <p class="note">Плюс журнал удалений: ${refs.journal}. После изменения сверка пересчитается.</p>`
    : '<p>Нет открытых папок. Откройте папку облака через «Открыть папку…» и дождитесь сканирования.</p>';
  if (!await confirmModal('Папки облака для сверки', html, 'Пересверить') || !roots.length) return;
  const sel = $$('#mBody .ref-list input:checked').map(x => x.value);
  try {
    const r = await api('/api/refs', { roots: sel.length === roots.length ? null : sel });
    toast(`Удалены в облаке: ${r.deleted}, есть в облаке: ${r.cloud}, новые: ${r.new}` + (r.premarked ? `<br>Помечено к удалению: ${r.premarked}` : ''));
    await loadItems();
  } catch (e) { toast(esc(e.message), true); }
}

function findRunning() { return ['connect', 'list', 'match', 'verify'].includes(S.find.phase); }

function findBarHTML() {
  const f = S.find;
  if (!f || f.phase === 'idle') return '';
  let text;
  if (findRunning()) {
    const pct = f.total ? ` ${f.done} из ${f.total}` : '';
    text = `<span class="spin"></span> ${esc(f.message)}${pct}`;
  } else if (f.phase === 'done') {
    const scope = f.scope === 'months' ? ` Искал в папках за ${plural(f.months.length, 'месяц', 'месяца', 'месяцев')} съёмки этих файлов.` : '';
    const more = f.scope === 'months' && f.found < f.checked
      ? (f.wide ? '' : ' <button class="link" data-act="phone-find-wide" title="Для не найденных проверить все снимки телефона ±26 часов от даты съёмки — на случай сдвига часового пояса. Читает с телефона намного больше файлов.">Искать шире (±26 ч)</button>') +
        ' <button class="link" data-act="phone-find-all">Искать по всему телефону (долго)</button>' : '';
    const locked = f.unreadable ? ` <span style="color:var(--warn)"><b>${plural(f.unreadable, 'файл', 'файла', 'файлов')} не удалось прочитать с телефона</b> — похоже, iPhone заблокировался. Разблокируйте его, не давайте экрану погаснуть и нажмите «Найти на телефоне» ещё раз.</span>` : '';
    const mis = f.mismatch ? ` У ${plural(f.mismatch, 'файла', 'файлов', 'файлов')} совпали размер и дата, но не содержимое — их не удалим.` : '';
    const pairs = f.found ? ' <button class="link" data-act="ph-filter" data-v="found">Проверить пары «облако ↔ iPhone»</button>' : '';
    text = `На телефоне «${esc(f.device)}» найдено <b>${f.found}</b> из ${f.checked} — каждый прочитан с телефона целиком и совпал с облачным побайтно.${locked}${mis}${scope}${more}${pairs}<br> <span class="bdg phone">${PHONE_ICON}</span> — есть на телефоне, <span class="bdg nophone">${PHONE_ICON}<i>✕</i></span> — не найден.` +
      (f.found ? ' При удалении можно сразу удалить их и с телефона.' : '');
  } else {
    text = `<span style="color:var(--reject)">${esc(f.message)}</span>`;
  }
  return `<div class="find-bar">${text}</div>`;
}

/** Пары «облачный оригинал ↔ миниатюра прямо с iPhone» для проверки глазами перед удалением. */
function pairsHTML(list) {
  return `<p class="note pairs-note">Слева — файл из облака, справа — миниатюра, которую прислал сам iPhone для найденного файла.
    Каждый файл на телефоне прочитан целиком и совпал с облачным побайтно. Если что-то выглядит не так — нажмите «Не удалять».</p>
    <div class="pairs">${list.map(it => `
    <div class="pair" data-id="${it.id}">
      <div class="pair-imgs">
        <figure><img loading="lazy" src="${thumbUrl(it)}" alt=""><figcaption>Облако</figcaption></figure>
        <span class="pair-eq" title="Содержимое совпадает">=</span>
        <figure><img loading="lazy" src="${phoneThumbUrl(it)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'pair-noimg',textContent:'нет миниатюры'}))"><figcaption>iPhone</figcaption></figure>
      </div>
      <div class="pair-meta">
        <div class="pair-names"><b title="${esc(it.p)}">${esc(fileName(it))}</b><span>↔</span><b title="${esc(it.ph)}">${esc(it.ph.split('/').pop())}</b></div>
        <div class="muted">${fmtSize(it.sz)} · ${fmtDT(it.t)}${it.cx?.length ? ' · вместе с ' + it.cx.join(', ') : ''}</div>
        <div class="pair-ok"><svg class="i"><use href="#i-check"/></svg>файл совпадает с облачным побайтно (SHA-1)</div>
        ${it.k === 'v' ? '<div class="muted">у видео iPhone может показать другой кадр — это нормально</div>' : ''}
      </div>
      <button class="btn small ghost" data-act="keep" data-id="${it.id}" title="Убрать из корзины — не удалять ни в облаке, ни на телефоне">Не удалять</button>
    </div>`).join('')}</div>`;
}

// ---------- статистика: сколько места освобождено
async function loadStats() {
  try { S.stats = await api('/api/stats'); } catch { return; }
  const el = $('#statsCard');
  if (el) el.outerHTML = statsCardHTML();
}

function statTotals(source) {
  const rows = (S.stats?.totals || []).filter(t => t.source === source);
  return { cloud: rows.find(t => t.what === 'cloud'), phone: rows.find(t => t.what === 'phone') };
}

const approx = t => (t?.estimated ? '≈ ' : '');

function statsCardHTML() {
  const st = S.stats;
  if (!st) return '<div class="stats-card" id="statsCard"></div>';
  const { cloud, phone } = statTotals(st.source);
  if (!cloud && !phone) {
    return `<div class="stats-card" id="statsCard"><span class="muted">Здесь будет видно, сколько места освобождено в этой папке и на телефоне.</span>
      ${st.totals.length ? '<button class="link" data-act="stats-more">Все папки</button>' : ''}</div>`;
  }
  const block = (label, value, sub) => `<div class="stat"><div class="stat-l">${label}</div><div class="stat-v">${value}</div><div class="stat-s">${sub}</div></div>`;
  const parts = [];
  if (cloud) parts.push(block(S.source?.kind === 'folder' ? 'Освобождено в папке' : 'Освобождено', approx(cloud) + fmtSize(cloud.bytes),
    `${plural(cloud.files, 'файл', 'файла', 'файлов')} · ${plural(cloud.sessions, 'очистка', 'очистки', 'очисток')}`));
  if (phone) parts.push(block('Освобождено на телефоне', fmtSize(phone.bytes), `${plural(phone.files, 'файл', 'файла', 'файлов')}`));
  if (cloud && st.folder_size != null) {
    const before = st.folder_size + cloud.bytes;
    // два знака после запятой: иначе «22 ГБ → 22 ГБ»
    const precise = b => (b >= 1024 ** 3 ? (b / 1024 ** 3).toFixed(2).replace('.', ',') + ' ГБ' : fmtSize(b));
    parts.push(block('Размер папки', `${precise(before)} → ${precise(st.folder_size)}`,
      `−${(cloud.bytes / before * 100).toFixed(cloud.bytes / before < 0.01 ? 2 : 1).replace('.', ',')} %`));
  }
  return `<div class="stats-card" id="statsCard">${parts.join('')}<div class="spacer"></div>
    <button class="btn small ghost" data-act="stats-more">Подробнее</button></div>`;
}

async function statsModal() {
  if (!S.stats) await loadStats();
  const st = S.stats;
  const sources = [...new Set(st.totals.map(t => t.source))];
  const fdt = t => new Date(t * 1000).toLocaleString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const folders = sources.map(src => {
    const { cloud, phone } = statTotals(src);
    const name = src.split(/[\\/]/).filter(Boolean).pop() || src;
    return `<tr${src === st.source ? ' class="cur"' : ''}><td title="${esc(src)}">${esc(name)}</td>
      <td>${cloud ? approx(cloud) + fmtSize(cloud.bytes) : '—'}</td><td>${cloud ? cloud.files : '—'}</td>
      <td>${phone ? fmtSize(phone.bytes) : '—'}</td><td>${phone ? phone.files : '—'}</td></tr>`;
  }).join('');
  const sum = (what, key) => st.totals.filter(t => t.what === what).reduce((a, t) => a + (t[key] || 0), 0);
  const hist = st.history.map(h => `<tr><td>${fdt(h.at)}</td><td>${h.what === 'phone' ? 'телефон' + (h.device ? ` «${esc(h.device)}»` : '') : 'папка'}</td>
      <td>${h.estimated ? '≈ ' : ''}${fmtSize(h.bytes)}</td><td>${h.files}</td></tr>`).join('');
  const html = `
    <table class="stats-table"><thead><tr><th>Папка</th><th>Освобождено</th><th>Файлов</th><th>С телефона</th><th>Файлов</th></tr></thead>
      <tbody>${folders}</tbody>
      <tfoot><tr><td>Всего</td><td>${fmtSize(sum('cloud', 'bytes'))}</td><td>${sum('cloud', 'files')}</td>
        <td>${fmtSize(sum('phone', 'bytes'))}</td><td>${sum('phone', 'files')}</td></tr></tfoot></table>
    ${hist ? `<h4 class="stats-h">История очисток: ${esc((st.source || '').split(/[\\/]/).filter(Boolean).pop() || '')}</h4>
      <div class="stats-hist"><table class="stats-table"><thead><tr><th>Когда</th><th>Откуда</th><th>Освобождено</th><th>Файлов</th></tr></thead><tbody>${hist}</tbody></table></div>` : ''}
    ${st.totals.some(t => t.estimated) ? '<p class="note">≈ — восстановлено из журнала удалений: без парных RAW и Live-видео, удаления с телефона тогда не учитывались.</p>' : ''}`;
  await confirmModal('Сколько места освобождено', html, 'Закрыть', false, false);
}

let findTimer = null;
async function phoneFind(everywhere = false, wide = false) {
  try {
    S.find = await api('/api/phone-find', { everywhere, wide });
  } catch (e) { toast(esc(e.message), true); return; }
  pollFind();
}

async function pollFind() {
  clearTimeout(findTimer);
  try { S.find = await api('/api/phone-find'); } catch { return; }
  if (!findRunning()) {
    S.find.stamp = Date.now();
    if (S.find.phase === 'done' && S.find.found && !pollFind.shown) { pollFind.shown = true; S.phoneFilter = 'found'; }
  } else {
    pollFind.shown = false;
  }
  if (S.view === 'trash') {
    const bar = $('.find-bar');
    if (bar) bar.outerHTML = findBarHTML(); else rerenderKeepScroll();
  }
  if (findRunning()) findTimer = setTimeout(pollFind, 800);
  else { await loadItems(); }
}

function resetForNewSource() {
  S.find = { phase: 'idle' };
  S.search = null; S.aiSel.clear(); S.aiInfo = null; $('#aiQuery').value = '';
  S.phoneFilter = 'all';
  S.items = []; S.byId = new Map(); S.series = new Map(); S.dups = new Map();
  S.selected.clear(); S.undo = []; S.focus = null; S.folder = { size: null, files: null }; S.match = null;
  lastVersion = -1;
  mapDirty = true;
}

async function pickFolder() {
  toast('Открывается окно выбора папки…', false, 2000);
  try {
    const r = await api('/api/pick-folder');
    if (r.path) openFolder(r.path);
  } catch (e) { toast(esc(e.message), true); }
}

async function openFolder(path) {
  try {
    const st = await api('/api/open', { path });
    S.items = []; S.byId = new Map(); S.series = new Map(); S.dups = new Map();
    S.selected.clear(); S.undo = []; S.focus = null; S.folder = { size: null, files: null }; S.match = null;
    S.find = { phase: 'idle' };
    S.search = null; S.aiSel.clear(); S.aiInfo = null; $('#aiQuery').value = '';
    lastVersion = -1;
    mapDirty = true;
    applyState(st);
    setView('grid');
  } catch (e) { toast(esc(e.message), true); }
}

// ============================================================ «Можно удалить»
// Подсказки по неразобранным снимкам. Каждый снимок — только в первой подходящей группе, порядок — от самых
// очевидных к тем, что стоит проверить глазами. Ничего не удаляется: «Отметить» кладёт в «Корзину отбора».
const YEAR = 365 * 864e2;
const CLEAN = [
  { key: 'junk', emoji: '🌀', title: 'Случайные кадры', hint: 'Смаз, темнота, палец на объективе, снимки пола — по оценке AI.',
    test: it => it.ai === 'junk' },
  { key: 'eyes', emoji: '😴', title: 'Закрыты глаза, а в серии есть кадр лучше', hint: 'В той же серии есть кадр, где у всех открыты глаза.',
    test: it => it.qc > 0 && it.s && !it.sb && (S.series.get(it.s) || []).some(x => x.id !== it.id && !(x.qc > 0)) },
  { key: 'series', emoji: '🎞️', title: 'Лишние кадры серий', hint: 'Всё, кроме рекомендованных ★. Если хочется сравнить кадры рядом — откройте серию (S).',
    test: it => it.s && !it.sb },
  { key: 'exact', emoji: '⧉', title: 'Точные копии', hint: 'Тот же файл байт в байт; остаётся лучшая копия ★.',
    test: it => it.d && it.dx && !it.db },
  { key: 'near', emoji: '🗜️', title: 'Пересжатые копии', hint: 'Та же картинка хуже качеством — например, из WhatsApp; остаётся лучшая ★.',
    test: it => it.d && !it.dx && !it.db },
  { key: 'shots', emoji: '📱', title: 'Скриншоты старше года', hint: 'Обычно нужны ненадолго: маршруты, брони, переписка.',
    test: it => it.c === 'screenshot' && it.t != null && it.t < Date.now() / 1000 - YEAR },
  { key: 'memes', emoji: '😂', title: 'Мемы и картинки из мессенджеров', hint: 'Пересланные картинки, не ваши снимки.',
    test: it => it.c === 'messenger' && (it.ai === 'memes' || it.ai === 'screens') },
  { key: 'screens', emoji: '💻', title: 'Фото экранов', hint: 'Сфотографированный монитор или телефон — по оценке AI.',
    test: it => it.c === 'photo' && it.ai === 'screens' },
];
const CLEAN_PREVIEW_ROWS = 2;

function cleanSections() {
  const secs = CLEAN.map(c => ({ ...c, items: [] }));
  for (const it of S.items) {
    if (it.st !== 0 || !passFilters(it, false)) continue;
    const sec = secs.find(c => c.test(it));
    if (sec) sec.items.push(it);
  }
  return secs.filter(c => c.items.length);
}

function renderClean() {
  const el = $('#view-clean');
  const secs = cleanSections();
  const total = secs.reduce((n, c) => n + c.items.length, 0);
  const size = secs.reduce((n, c) => n + c.items.reduce((a, i) => a + (i.sz || 0), 0), 0);
  const analyzed = S.items.some(i => i.ai), rated = S.items.some(i => i.qf != null);
  const aiNote = !analyzed || !rated
    ? `<div class="clean-note ai">✨ ${!analyzed ? 'Случайные кадры, мемы и фото экранов находит нейросеть' : 'Кадры с закрытыми глазами находит оценка кадра'} — запустите ${!analyzed ? 'анализ' : 'оценку'} на <button class="link" data-act="go-ai">странице «AI»</button>.</div>` : '';
  S.list = [];
  // в карточке плиток в ряду меньше, чем в ленте (поля карточки) — превью ровно в CLEAN_PREVIEW_ROWS ряда
  const gap = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--gap')) || 4;
  const thumb = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--thumb')) || 160;
  const perRow = Math.max(1, Math.floor((el.clientWidth - 44 - 34 + gap) / (thumb + gap)));
  let html = `<div class="list-head"><h2>Можно удалить</h2>
    <span class="sub">${total ? `${plural(total, 'файл', 'файла', 'файлов')} · ${fmtSize(size)}` : ''}</span><div class="spacer"></div>
    ${total ? `<button class="btn reject" data-act="clean-mark" title="Отметить к удалению всё, что на этой странице">Отметить всё к удалению</button>` : ''}</div>
    <div class="clean-note">Подсказки по неразобранным снимкам. «Отметить» кладёт их в <b>Корзину отбора</b> — сами файлы удаляются только после подтверждения там. Клавиши как в ленте: <kbd>X</kbd> удалить, <kbd>P</kbd> оставить, двойной клик — открыть.</div>${aiNote}`;
  if (!total) html += emptyHTML('Пока нечего предложить', 'Здесь появятся случайные кадры, лишние кадры серий, копии, старые скриншоты и мемы из мессенджеров.');
  for (const sec of secs) {
    const open = S.cleanOpen.has(sec.key), limit = CLEAN_PREVIEW_ROWS * perRow;
    const shown = open ? sec.items : sec.items.slice(0, limit);
    S.list.push(...shown);
    const secSize = sec.items.reduce((a, i) => a + (i.sz || 0), 0);
    html += `<section class="card clean-sec"><div class="card-head"><span class="emo clean-emo">${sec.emoji}</span>
        <div class="clean-t"><span class="t">${sec.title}</span><span class="sub">${sec.hint}</span></div><div class="spacer"></div>
        <span class="pill">${plural(sec.items.length, 'файл', 'файла', 'файлов')} · ${fmtSize(secSize)}</span>
        <button class="btn small reject" data-act="clean-mark" data-sec="${sec.key}">Отметить ${sec.items.length > 1 ? 'все' : ''}</button></div>
      <div class="tiles">${tilesHTML(shown, 0, shown.length, { noSeries: sec.key !== 'series' && sec.key !== 'eyes' })}</div>
      ${sec.items.length > limit ? `<button class="link clean-more" data-act="clean-more" data-sec="${sec.key}">${open ? 'Свернуть' : `Показать все ${sec.items.length}`}</button>` : ''}</section>`;
  }
  el.innerHTML = html;
}

async function cleanMark(secs) {
  const items = secs.flatMap(c => c.items);
  if (!items.length) return;
  const size = items.reduce((a, i) => a + (i.sz || 0), 0);
  const what = secs.length === 1 ? `«${secs[0].title}»` : 'все подсказки';
  if (items.length > 20 && !await confirmModal('Отметить к удалению?', `<p>${what}: ${plural(items.length, 'файл', 'файла', 'файлов')}, ${fmtSize(size)}.</p><p class="muted">Они попадут в «Корзину отбора» — там их можно проверить и вернуть. Файлы удаляются только после подтверждения.</p>`, 'Отметить')) return;
  await applyStatuses(new Map(items.map(i => [i.id, 2])));
  toast(`${plural(items.length, 'файл', 'файла', 'файлов')} в «Корзине отбора» · ${fmtSize(size)}`);
}

// ============================================================ нейросеть (страница «AI»)
let aiTimer = null;

async function aiCall(url, body = {}) {
  try { S.aiInfo = await api(url, body); } catch (e) { toast(esc(e.message), true); }
  aiPoll();
}

/** Опрос /api/ai, пока открыта страница или идёт установка/анализ. По окончании анализа список перечитывается. */
async function aiPoll() {
  clearTimeout(aiTimer);
  aiTimer = null;
  const wasRunning = S.aiInfo?.analysis?.running;
  const wasRating = S.aiInfo?.quality?.running;
  let info;
  try { info = await api('/api/ai'); } catch { return; }
  const changed = JSON.stringify(info) !== JSON.stringify(S.aiInfo);
  S.aiInfo = info;
  S.ai.ready = info.packages && info.installed;
  const a = info.analysis, q = info.quality;
  const running = info.busy || info.faces?.busy || a?.running || q?.running;
  if (wasRunning && !a?.running) {
    loadItems();
    if (a?.phase === 'done') toast('Анализ завершён: категории по смыслу и поиск — в панели слева');
    if (a?.phase === 'error') toast(esc(a.message), true, 8000);
  } else if (wasRating && !q?.running) {
    loadItems();
    if (q?.phase === 'done') toast(`Оценка завершена${q.closed ? `: закрыты глаза на ${plural(q.closed, 'кадре', 'кадрах', 'кадрах')}` : ''}. ★ в сериях выбраны заново`);
    if (q?.phase === 'error') toast(esc(q.message), true, 8000);
  } else if ((a?.running || q?.running) && Date.now() - lastLoad > 8000) loadItems();
  for (const job of [info.job, info.faces?.job]) {
    if (job?.phase === 'error' && aiPoll.shownErr !== job.message) {
      aiPoll.shownErr = job.message;
      toast(esc(job.message), true, 10000);
    }
  }
  if (S.view === 'ai' && changed) renderAI();  // без изменений не перерисовываем: иначе сбивается наведение на кнопки
  const job = a?.running ? a : q?.running ? q : null;
  $('#cnt-ai').textContent = job && job.total ? Math.floor(job.done / job.total * 100) + ' %' : running ? '…' : '';
  if (running || S.view === 'ai') aiTimer = setTimeout(aiPoll, running ? 800 : 3000);
}

// Запрос похож на имя файла (цифры, IMG_1234, DSC…): по смыслу такое не ищем, иначе в выдаче сотни случайных снимков
const FILE_QUERY = /^[\d\s._-]+$|^(img|dsc|dscn|pxl|mvimg|vid|photo|video|screenshot)[\s_-]?\d/i;

/** Показать в ленте снимки, похожие на этот: сам снимок первым, дальше — по убыванию сходства. */
async function findSimilar(it) {
  if (!it) return;
  if (!it.ai) { toast(S.items.some(i => i.ai) ? 'Этот снимок ещё не проанализирован — запустите анализ новых на странице «AI»' : 'Похожие снимки ищет нейросеть: скачайте модель и запустите анализ на странице «AI»', true, 6000); return; }
  let r;
  try { r = await api(`/api/ai/similar/${it.id}`); } catch (e) { toast(esc(e.message), true); return; }
  showFound([it.id, ...r.hits.map(([id]) => id)], `Похожие на «${fileName(it)}»`);
}

/** Поиск по картинке, перетащенной в окно из проводника или браузера. */
async function findByImage(file) {
  const hint = toast(`Ищу снимки, похожие на «${esc(file.name)}»…`, false, 30000);
  try {
    const r = await fetch('/api/ai/similar-image', { method: 'POST', body: file });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);
    showFound(j.hits.map(([id]) => id), `Похожие на «${file.name}»`);
  } catch (e) { toast(esc(e.message), true, 6000); } finally { hint.remove(); }
}

function showFound(ids, title) {
  runSearch.seq = (runSearch.seq || 0) + 1;  // отменить незаконченный текстовый поиск
  $('#aiQuery').value = '';
  S.search = { q: title, title, rank: new Map(ids.map((id, i) => [id, i])) };
  S.view = 'grid';
  filtersChanged();
  $('#view-grid').scrollTop = 0;
}

/** Поиск: сначала совпадения по имени файла, затем (если папка проанализирована) — по описанию того, что на снимке. */
async function runSearch(q) {
  const seq = runSearch.seq = (runSearch.seq || 0) + 1;  // ответ на устаревший запрос не применяем
  $('#aiSearch').classList.remove('busy');
  q = q.trim();
  if (!q) { S.search = null; filtersChanged(); return; }
  const ql = q.toLowerCase();
  const ids = S.items.filter(i => i.p.toLowerCase().includes(ql)).map(i => i.id);
  if (S.items.some(i => i.ai) && !FILE_QUERY.test(q)) {
    $('#aiSearch').classList.add('busy');  // первый запрос ждёт загрузки модели, это пара секунд
    try {
      const r = await api('/api/ai/search', { q });
      ids.push(...r.hits.map(([id]) => id));
    } catch (e) { toast(esc(e.message), true); }
    if (seq === runSearch.seq) $('#aiSearch').classList.remove('busy');
  }
  if (seq !== runSearch.seq) return;
  const uniq = [...new Set(ids)];
  S.search = { q, rank: new Map(uniq.map((id, i) => [id, i])) };
  if (!uniq.length) toast('Ничего не нашлось');
  S.view = 'grid';
  filtersChanged();
  $('#view-grid').scrollTop = 0;
}

function aiBar(done, total, indet) {
  return `<div class="bar ai-bar${indet ? ' indet' : ''}"><i style="width:${indet ? '' : (total ? done / total * 100 : 0).toFixed(1) + '%'}"></i></div>`;
}

/** Состояние скачивания модели: прогресс, «Скачать» или «Удалить». */
function modelBlock(m, model, what) {
  const job = m.job || {};
  if (m.busy) {
    const det = job.phase === 'download' && job.total;
    return `<p>${esc(job.message)}${det ? ` <span class="mono">${fmtSize(job.done)} из ${fmtSize(job.total)}</span>` : ''}</p>${aiBar(job.done, job.total, !det)}
      <p class="muted">Установка идёт в фоне, разбор можно продолжать. Если скачивание прервётся, оно продолжится с того же места.</p>`;
  }
  if (m.packages && m.installed) {
    return `<p>Модель скачана${m.size ? ` (${fmtSize(m.size)})` : ''}.${m.device ? ` Считает: <b>${esc(m.device)}</b>.` : ''}</p>
      <div class="ai-acts"><button class="btn small ghost" data-act="ai-remove" data-model="${model}">Удалить модель</button></div>`;
  }
  return `<p>${what}${job.phase === 'error' ? `<br><span class="err-text">${esc(job.message)}</span>` : ''}</p>
    <div class="ai-acts"><button class="btn primary" data-act="ai-install" data-model="${model}"><svg class="i"><use href="#i-sparkles"/></svg>${job.phase === 'error' ? 'Повторить' : 'Скачать модель'}</button></div>`;
}

/** Прогресс и кнопка запуска для анализа папки (SigLIP) и оценки кадров (лица). */
function jobBlock(a, ready, startAct, stopAct, verb, what) {
  const left = Math.max(0, a.total - a.done);
  const rate = a.running && a.done > 0 && a.started ? a.done / (Date.now() / 1000 - a.started) : 0;
  const eta = rate && a.total > a.done ? ` · осталось ≈ ${fmtDur((a.total - a.done) / rate) || '0:01'}` : '';
  const head = a.running
    ? (a.phase === 'load' ? 'Загрузка модели…' : `${esc(a.message)} · ${a.done} из ${a.total}${eta}`)
    : !left && a.total ? `Все ${what} обработаны: <b>${a.total}</b>`
      : `Обработано <b>${a.done}</b> из ${a.total}${left && a.done ? ` · новых: ${left}` : ''}`;
  const btn = a.running
    ? `<button class="btn" data-act="${stopAct}">Остановить</button>`
    : !left && a.total ? ''
      : `<button class="btn primary" data-act="${startAct}" ${ready ? '' : 'disabled'}><svg class="i"><use href="#i-sparkles"/></svg>${a.done ? `${verb} новые (${left})` : `${verb} ${what}`}</button>`;
  return `<p>${head}</p>${aiBar(a.done, a.total, a.running && a.phase === 'load')}
    ${btn ? `<div class="ai-acts">${btn}</div>` : ''}
    ${a.phase === 'error' ? `<p class="err-text">${esc(a.message)}</p>` : ''}`;
}

function renderAI() {
  const el = $('#view-ai');
  const info = S.aiInfo;
  if (!info) { el.innerHTML = '<div class="list-head"><h2>AI</h2></div>'; if (!aiTimer) aiPoll(); return; }
  if (!aiTimer) aiTimer = setTimeout(aiPoll, 3000);
  const ready = info.packages && info.installed;
  const installing = info.busy;
  const a = info.analysis;
  const pillOf = m => (m.busy ? '<span class="pill">Установка…</span>' : m.packages && m.installed ? '<span class="pill ok">Установлена</span>' : '<span class="pill">Не установлена</span>');

  // 1. Модель SigLIP
  const model = modelBlock(info, 'siglip2-base-224', `Скачивается один раз: модель (≈ ${fmtSize(info.download_size)}) и ONNX Runtime для расчётов на видеокарте.`);
  const pill = pillOf(info);

  // 2. Анализ папки
  const analysis = !S.root || !a ? '<p class="muted">Откройте папку, чтобы проанализировать её снимки.</p>'
    : a.no_aes && !a.running && a.done >= a.total
      ? `<p>Снимки проанализированы, но без оценки красоты кадра (она появилась позже): ${a.no_aes}. Досчитать её можно за секунды.</p>
        <div class="ai-acts"><button class="btn primary" data-act="ai-analyze" ${ready && !installing ? '' : 'disabled'}><svg class="i"><use href="#i-sparkles"/></svg>Досчитать</button></div>`
      : jobBlock(a, ready && !installing, 'ai-analyze', 'ai-stop', 'Проанализировать', 'снимки') + (!ready ? '<p class="muted">Сначала скачайте модель.</p>' : '');

  // 4. Оценка кадра: модели лиц и запуск
  const f = info.faces, q = info.quality;
  const facesReady = f.packages && f.installed;
  let rate = '';
  if (facesReady && S.root && q) {
    rate = jobBlock(q, !f.busy, 'ai-rate', 'ai-rate-stop', 'Оценить', 'фото');
    if (q.done) {
      rate += `<div class="ai-chips">${QUALITY_CATS.map(c => {
        const n = c.key === 'q:closed' ? q.closed : S.items.filter(c.test).length;
        return n ? `<button class="ai-chip" data-act="q-cat" data-key="${c.key}">${markHTML(c)}${c.label}<span class="cnt">${n}</span></button>` : '';
      }).join('')}</div>`;
    }
  } else if (facesReady) rate = '<p class="muted">Откройте папку, чтобы оценить её снимки.</p>';

  // 3. Категории по смыслу
  const counts = a?.labels || {};
  const cats = aiLabels().filter(l => counts[l.key]).map(l =>
    `<button class="ai-chip" data-act="ai-cat" data-key="${l.key}">${markHTML(l)}${l.label}<span class="cnt">${counts[l.key]}</span></button>`).join('');

  el.innerHTML = `<div class="list-head"><h2>AI</h2><span class="sub">Нейросеть работает на этом компьютере: снимки никуда не отправляются</span></div>
    <div class="ai-grid">
      <div class="card ai-card"><div class="card-head"><span class="t">Модель SigLIP 2</span>${pill}</div>
        <p class="muted">Понимает, что на снимке: раскладывает снимки по категориям и ищет по описанию на русском и английском.</p>${model}</div>
      <div class="card ai-card"><div class="card-head"><span class="t">Анализ папки</span>${S.root ? `<span class="sub" title="${esc(S.root)}">${esc(S.root.split(/[\\/]/).filter(Boolean).pop())}</span>` : ''}</div>
        <p class="muted">Модель смотрит на миниатюры из индекса, сами файлы не читаются. Снимки, добавленные позже, анализируются этой же кнопкой.</p>${analysis}</div>
      <div class="card ai-card wide"><div class="card-head"><span class="t">Категории по смыслу</span></div>
        ${cats ? `<div class="ai-chips">${cats}</div><p class="muted">Клик — показать в ленте. Поиск по описанию — в панели слева, раздел «По смыслу».</p>`
          : '<p class="muted">Появятся после анализа: люди, животные, еда, природа, город, транспорт, документы и чеки, экраны, мемы, случайные кадры.</p>'}</div>
      <div class="card ai-card wide"><div class="card-head"><span class="t">Оценка кадра</span>${pillOf(f)}</div>
        <p class="muted">Находит лица и проверяет, не закрыты ли глаза и не смазаны ли лица. Вместе с красотой кадра по SigLIP это решает, какой кадр серии отметить ★. Для снимков с людьми читается оригинал, остальные оцениваются по миниатюре.</p>
        ${modelBlock(f, 'faces', `Скачиваются две маленькие модели лиц (≈ ${fmtSize(f.download_size)}): YuNet и MediaPipe Face Mesh.${f.packages ? '' : ' Если ONNX Runtime ещё не установлен, он поставится вместе с ними.'}`)}
        ${rate}</div>
    </div>`;
}

// ============================================================ привязка интерфейса
function bindUI() {
  layoutTiles();
  new ResizeObserver(() => layoutTiles()).observe($('#main'));
  $('#collapseSeries').checked = S.collapse;
  $('#sortOrder').value = S.sort;

  // приветствие: кнопка «Назад», если папка уже открыта
  const back = document.createElement('button');
  back.id = 'welcomeBack';
  back.className = 'btn ghost';
  back.textContent = '← Назад';
  back.style.marginTop = '18px';
  back.onclick = () => showWelcome(false);
  $('.welcome-card').appendChild(back);

  $('#btnOpen').onclick = () => showWelcome(true);
  $('#btnOpen2').onclick = pickFolder;
  $('#btnPathGo').onclick = () => { const p = $('#pathInput').value.trim().replace(/^"|"$/g, ''); if (p) openFolder(p); };
  $('#pathInput').addEventListener('keydown', e => { if (e.key === 'Enter') $('#btnPathGo').click(); });
  $('#recentList').addEventListener('click', e => { const b = e.target.closest('[data-path]'); if (b) openFolder(b.dataset.path); });
  $('#btnRescan').onclick = async () => { try { handleScan(await api('/api/rescan', {})); } catch (e) { toast(esc(e.message), true); } };
  $('#btnTrashTop').onclick = () => setView('trash');
  $('#btnPhone').onclick = phoneDialog;
  $('#btnPhone2').onclick = phoneDialog;
  $('#btnRefs').onclick = refsDialog;
  $('#btnMarkCloud').onclick = async () => {
    const ids = S.items.filter(i => i.m === 'cloud' && i.st !== 2).map(i => i.id);
    if (!ids.length) return;
    if (await confirmModal('Пометить к удалению?', `<p>${plural(ids.length, 'снимок', 'снимка', 'снимков')} уже сохранены в облаке — на телефоне это лишние копии.</p><p class="muted">Пока это только отметка: удаление с телефона — после подтверждения в «Корзине отбора».</p>`, 'Пометить')) {
      mark(ids, 2);
    }
  };
  $('#matchList').addEventListener('click', e => {
    const c = e.target.closest('[data-match]'); if (!c) return;
    S.match = S.match === c.dataset.match ? null : c.dataset.match;
    filtersChanged();
  });
  $('#btnUndo').onclick = undo;

  $('#views').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setView(b.dataset.view); });
  const toggle = (set, k) => { if (set.has(k)) set.delete(k); else set.add(k); };
  const toFeed = () => { if (!['grid', 'map'].includes(S.view)) S.view = 'grid'; };
  $('#cats').addEventListener('click', e => {
    const c = e.target.closest('[data-cat]'); if (!c) return;
    toggle(S.types, c.dataset.cat);
    toFeed();
    filtersChanged();
  });
  const aiClick = e => {
    if (e.target.closest('[data-ai-more]')) { S.aiMore = !S.aiMore; renderAiSide(); return; }
    const c = e.target.closest('[data-ai]'); if (!c) return;
    toggle(S.aiSel, c.dataset.ai);
    toFeed();
    filtersChanged();
  };
  $('#aiCats').addEventListener('click', aiClick);
  $('#qCats').addEventListener('click', aiClick);
  $('#aiSearch').addEventListener('submit', e => { e.preventDefault(); runSearch($('#aiQuery').value); });
  $('#aiQuery').addEventListener('keydown', e => { if (e.key === 'Escape') { $('#aiQuery').value = ''; runSearch(''); $('#aiQuery').blur(); } });
  $('#aiQuery').addEventListener('search', e => { if (!e.target.value) runSearch(''); });  // крестик в поле
  $('#fltReset').onclick = resetFilters;
  $('#statusFilter').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S.status = b.dataset.v;
    filtersChanged();
  });
  $('#periodChips').addEventListener('click', e => {
    const pick = periodPick();
    const yb = e.target.closest('[data-year]'), mb = e.target.closest('[data-month]');
    if (yb) {
      const y = +yb.dataset.year;
      [S.from, S.to] = pick?.y === y && pick.m == null ? ['', ''] : [`${y}-01-01`, `${y}-12-31`];
    } else if (mb) {
      const [y, m] = mb.dataset.month.split('-').map(Number);
      // повторный клик по месяцу — обратно к году (или ко всему периоду, если год в папке один)
      const multiYear = $$('#periodChips [data-year]').length > 0;
      [S.from, S.to] = pick?.y === y && pick.m === m ? (multiYear ? [`${y}-01-01`, `${y}-12-31`] : ['', '']) : monthRange(y, m);
      CAL.month = `${y}-${pad2(m + 1)}`;
      CAL.mode = 'days';
    } else return;
    CAL.anchor = null;
    toFeed();
    filtersChanged();
  });
  $('#calToggle').onclick = () => { CAL.open = !CAL.open; prefs.set('calOpen', CAL.open); renderCalendar(); };
  const cal = $('#calendar');
  cal.addEventListener('click', e => {
    const nav = e.target.closest('[data-cal-nav]');
    if (nav) { calShift(+nav.dataset.calNav); return; }
    if (e.target.closest('[data-cal-cancel]')) { CAL.anchor = null; renderCalendar(); return; }
    if (e.target.closest('[data-cal-clear]')) { CAL.anchor = null; S.from = S.to = ''; filtersChanged(); return; }
    if (e.target.closest('[data-cal-zoom]')) {
      // «отдалить»: дни → месяцы года → годы
      if (CAL.mode === 'days') { CAL.mode = 'months'; CAL.year = +CAL.month.slice(0, 4); } else CAL.mode = 'years';
      renderCalendar();
      return;
    }
    const oy = e.target.closest('[data-cal-open-year]');
    if (oy) { CAL.year = +oy.dataset.calOpenYear; CAL.mode = 'months'; renderCalendar(); return; }
    const om = e.target.closest('[data-cal-open-month]');
    if (om) { CAL.month = om.dataset.calOpenMonth; CAL.mode = 'days'; renderCalendar(); return; }
    if (e.target.closest('[data-cal-month]')) {
      CAL.anchor = null;
      [S.from, S.to] = monthRange(+CAL.month.slice(0, 4), +CAL.month.slice(5, 7) - 1);
      toFeed();
      filtersChanged();
      return;
    }
    const day = e.target.closest('[data-cal-day]');
    if (!day) return;
    const k = day.dataset.calDay;
    if (!CAL.anchor) {
      // первый клик: сразу показываем этот день и ждём второй — конец периода
      CAL.anchor = k;
      S.from = S.to = k;
    } else {
      [S.from, S.to] = k < CAL.anchor ? [k, CAL.anchor] : [CAL.anchor, k];
      CAL.anchor = null;
    }
    toFeed();
    filtersChanged();
  });
  cal.addEventListener('mouseover', e => { const d = e.target.closest('[data-cal-day]'); if (d && CAL.anchor) paintRange(d.dataset.calDay); });
  cal.addEventListener('mouseleave', () => { if (CAL.anchor) paintRange(); });
  // картинку из проводника можно бросить в окно — найдём похожие снимки в папке
  let dragDepth = 0;
  const isFileDrag = e => S.root && [...(e.dataTransfer?.types || [])].includes('Files');
  document.addEventListener('dragenter', e => { if (!isFileDrag(e)) return; e.preventDefault(); dragDepth++; $('#dropHint').classList.remove('hidden'); });
  document.addEventListener('dragover', e => { if (isFileDrag(e)) e.preventDefault(); });
  document.addEventListener('dragleave', e => { if (isFileDrag(e) && --dragDepth <= 0) { dragDepth = 0; $('#dropHint').classList.add('hidden'); } });
  document.addEventListener('drop', e => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    dragDepth = 0;
    $('#dropHint').classList.add('hidden');
    const file = [...e.dataTransfer.files].find(f => /^image\//.test(f.type) || /\.(heic|heif|jpe?g|png|webp)$/i.test(f.name));
    if (!file) { toast('Перетащите картинку: JPEG, PNG, HEIC или WebP', true); return; }
    if (!S.items.some(i => i.ai)) { toast('Поиск по картинке работает после анализа на странице «AI»', true, 6000); return; }
    findByImage(file);
  });
  // всплывающие карточки внизу панели: открыта только одна, клик мимо — закрыть
  $$('.sb-pop').forEach(d => d.addEventListener('toggle', () => { if (d.open) $$('.sb-pop').forEach(o => { if (o !== d) o.open = false; }); }));
  document.addEventListener('mousedown', e => { if (!e.target.closest('.sb-pop')) $$('.sb-pop[open]').forEach(d => { d.open = false; }); });
  $('#colsRange').oninput = e => setCols(+e.target.value);
  $('#granSeg').addEventListener('click', e => {
    const b = e.target.closest('[data-g]');
    if (b) setCols(GRAN_COLS[b.dataset.g]);
  });
  $('#collapseSeries').onchange = e => { S.collapse = e.target.checked; prefs.set('collapse', S.collapse); render(); };
  $('#sortOrder').onchange = e => {
    S.sort = e.target.value;
    prefs.set('sort', S.sort);
    document.body.classList.toggle('sort-size', S.sort === 'size');
    render();
  };
  document.body.classList.toggle('sort-size', S.sort === 'size');
  $('#setDist').oninput = e => { $('#setDistVal').textContent = e.target.value; };
  $('#btnRegroup').onclick = async () => {
    try {
      await api('/api/settings', { series_gap: +$('#setGap').value, series_dist: +$('#setDist').value, series_keep: $('#setKeep').value });
      await loadItems();
      toast(`Найдено ${plural(S.series.size, 'серия', 'серии', 'серий')}, ${plural(S.dups.size, 'группа', 'группы', 'групп')} дубликатов`);
    } catch (e) { toast(esc(e.message), true); }
  };

  $('#sidebar').addEventListener('click', e => {
    const act = e.target.closest('[data-act]');
    if (act) onAction(act.dataset.act, act);
  });

  const main = $('#main');
  // Ctrl + колесо (и «щипок» на тачпаде) — сколько снимков в ряду
  let zoomAcc = 0;
  main.addEventListener('wheel', e => {
    if (!e.ctrlKey || !['grid', 'trash'].includes(S.view)) return;
    e.preventDefault();
    zoomAcc += e.deltaY;
    if (Math.abs(zoomAcc) < 40) return;
    const dir = zoomAcc > 0 ? 1 : -1;
    zoomAcc = 0;
    const i = ZOOM_LEVELS.reduce((best, v, j) => (Math.abs(v - S.cols) < Math.abs(ZOOM_LEVELS[best] - S.cols) ? j : best), 0);
    const next = ZOOM_LEVELS[Math.min(ZOOM_LEVELS.length - 1, Math.max(0, i + dir))];
    setCols(next, { x: e.clientX, y: e.clientY });
  }, { passive: false });
  main.addEventListener('click', e => {
    const act = e.target.closest('[data-act]');
    if (act) { onAction(act.dataset.act, act); return; }
    const ser = e.target.closest('.bdg.ser');
    if (ser) { openSeries(+ser.dataset.ser); return; }
    const fh = e.target.closest('[data-fold]');
    if (fh && !e.target.closest('[data-day]')) { setFolded([fh.dataset.fold], !S.folded.has(fh.dataset.fold)); return; }
    const day = e.target.closest('[data-day]');
    if (day) { selectDay(day.dataset.day); return; }
    const card = e.target.closest('.scard');
    if (card) { openSeries(+card.dataset.sid); return; }
    const tile = e.target.closest('.tile');
    if (!tile) return;
    const id = +tile.dataset.id;
    if (tile.closest('#mapStrip')) { openViewer(mapStripList, id); return; }
    const group = tile.closest('.card[data-gid]');
    if (group) { openViewer(S.dups.get(+group.dataset.gid) || [], id); return; }
    gridClick(e, id);
  });
  main.addEventListener('dblclick', e => {
    const tile = e.target.closest('.tile');
    if (!tile || !['grid', 'trash', 'clean'].includes(S.view)) return;
    const it = S.byId.get(+tile.dataset.id);
    if (S.collapse && it.s && S.view === 'grid') openSeries(it.s); else openViewer(S.list, it.id);
  });
  main.addEventListener('change', e => {
    if (e.target.id === 'onlyOpen') { S.onlyOpen = e.target.checked; prefs.set('onlyOpen', S.onlyOpen); render(); }
  });

  // миниатюра не загрузилась — показать имя файла
  document.addEventListener('error', e => {
    const img = e.target;
    if (img.tagName !== 'IMG' || !img.closest('.tile .img')) return;
    const it = S.byId.get(+img.closest('.tile').dataset.id);
    const d = document.createElement('div');
    d.className = 'noimg';
    d.textContent = it ? fileName(it) : '';
    img.replaceWith(d);
  }, true);

  // просмотрщик
  $('#viewer').addEventListener('click', e => {
    const act = e.target.closest('[data-act]');
    if (act) { onAction(act.dataset.act, act); return; }
    if (e.target.tagName === 'IMG' && e.target.closest('#vMedia')) vZoom(e);
    const ft = e.target.closest('.ft');
    if (ft) { V.idx = +ft.dataset.i; showV(); }
  });
  $('#vStrip').addEventListener('wheel', e => {
    if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { e.preventDefault(); $('#vStrip').scrollLeft += e.deltaY; }
  }, { passive: false });

  // разбор серии
  const rv = $('#review');
  rv.addEventListener('click', e => {
    const act = e.target.closest('[data-act]');
    if (act) { onAction(act.dataset.act, act); return; }
    const f = e.target.closest('.rf');
    if (f) toggleFrame(+f.dataset.id);
  });
  rv.addEventListener('dblclick', e => {
    const f = e.target.closest('.rf');
    if (!f) return;
    openViewer(R.items, +f.dataset.id);
  });
  $('#rGrid').addEventListener('mousemove', e => {
    if (!R.zoom) return;
    const box = e.target.closest('.rf-img');
    if (!box) return;
    // контейнеры кадров одинаковые, картинка вписана в них — одна и та же точка во всех кадрах
    const r = box.getBoundingClientRect();
    const x = Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100));
    const y = Math.min(100, Math.max(0, ((e.clientY - r.top) / r.height) * 100));
    $$('#rGrid .rf-img img').forEach(im => {
      im.style.transformOrigin = `${x}% ${y}%`;
      im.style.transform = `scale(${R.zf})`;
    });
  });
  $('#rGrid').addEventListener('wheel', e => {
    if (!R.zoom) return;
    e.preventDefault();
    R.zf = Math.min(10, Math.max(1.5, R.zf * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
    $$('#rGrid .rf-img img').forEach(im => { if (im.style.transform) im.style.transform = `scale(${R.zf})`; });
  }, { passive: false });

  document.addEventListener('keydown', e => {
    if (e.target.matches('input, select, textarea')) return;
    if (!$('#modal').classList.contains('hidden')) {
      if (e.key === 'Escape') M.resolve?.(false);
      if (e.key === 'Enter') { e.preventDefault(); M.resolve?.(true); }
      return;
    }
    if (!$('#viewer').classList.contains('hidden')) { viewerKey(e); return; }
    if (!$('#review').classList.contains('hidden')) { reviewKey(e); return; }
    if (!$('#welcome').classList.contains('hidden')) { if (e.key === 'Escape' && S.root) showWelcome(false); return; }
    if (e.key === 'Escape' && CAL.anchor) { CAL.anchor = null; renderCalendar(); return; }
    if (e.key === '/' && !e.ctrlKey && !e.altKey) { e.preventDefault(); $('#aiQuery').focus(); $('#aiQuery').select(); return; }
    if (S.view === 'ai' || S.view === 'stats') return;  // на этих страницах нет ленты снимков
    gridKey(e);
  });
}

/** Ширина плитки из числа колонок: ряд всегда заполняет ширину ленты. */
function layoutTiles() {
  const view = $('#view-grid');
  const w = (view.clientWidth || $('#main').clientWidth) - 44;  // минус боковые отступы ленты
  const gap = S.cols >= 12 ? 2 : S.cols >= 8 ? 3 : 4;
  const thumb = Math.max(24, Math.floor((w - gap * (S.cols - 1)) / S.cols));
  const root = document.documentElement.style;
  root.setProperty('--thumb', thumb + 'px');
  root.setProperty('--gap', gap + 'px');
  document.body.classList.toggle('tiles-tiny', thumb < 100);
  document.body.classList.toggle('tiles-micro', thumb < 60);
  $('#colsRange').value = S.cols;
  $('#colsVal').textContent = S.cols;
  const g = granularity();
  $$('#granSeg button').forEach(b => b.classList.toggle('active', b.dataset.g === g));
}

/** Плитка под точкой (или ближайшая к ней) — по геометрии, без elementFromPoint. */
function tileAt(view, x, y) {
  let best = null, bestD = Infinity;
  for (const t of view.querySelectorAll('.tile')) {
    const b = t.getBoundingClientRect();
    if (!b.width || b.bottom < 0 || b.top > innerHeight) continue;
    if (x >= b.left && x <= b.right && y >= b.top && y <= b.bottom) return t;
    const d = Math.hypot((b.left + b.right) / 2 - x, (b.top + b.bottom) / 2 - y);
    if (d < bestD) { bestD = d; best = t; }
  }
  return best;
}

/** Сменить число колонок, удерживая на месте снимок под курсором (или в центре). */
function setCols(n, point) {
  n = Math.max(2, Math.min(30, Math.round(n)));
  if (n === S.cols) return;
  const view = $('#view-' + S.view);
  const scrollable = view && ['grid', 'trash'].includes(S.view);
  let anchor = null, offset = 0;
  if (scrollable) {
    const r = view.getBoundingClientRect();
    const t = tileAt(view, point?.x ?? r.left + r.width / 2, point?.y ?? r.top + r.height / 2);
    if (t) { anchor = t.dataset.id; offset = t.getBoundingClientRect().top - r.top; }
  }
  const before = granularity();
  S.cols = n;
  prefs.set('cols', n);
  layoutTiles();
  // ленивая лента нарезана на блоки по целому числу рядов — при другом числе колонок её надо перестроить
  const rerendered = granularity() !== before || scrollable;
  if (rerendered) render();
  if (!anchor) return;
  const fix = () => {
    ensureTile(view, +anchor);
    const el = view.querySelector(`.tile[data-id="${anchor}"]`);
    for (let i = 0; el && i < 4; i++) {
      const d = el.getBoundingClientRect().top - view.getBoundingClientRect().top - offset;
      if (Math.abs(d) < 1) break;
      view.scrollTop += d;
    }
    fillVisible(view);
  };
  fix();
  // после перерисовки настоящая высота блоков известна только со следующего кадра
  if (rerendered) requestAnimationFrame(() => requestAnimationFrame(fix));
}

function filtersChanged() {
  prefs.set('types', [...S.types]);
  mapDirty = true;
  renderCats();
  render();
}

init();
