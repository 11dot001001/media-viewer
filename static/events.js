'use strict';

const E = { mode: 'trips', groups: null, active: null, chosen: new Set(), dirty: true,
  preferences: {min_count: 10, occasion_day: '', occasion_name: ''},
  loading: false, busy: false, error: '', request: 0 };

function resetEvents() {
  E.request++;
  Object.assign(E, { groups: null, active: null, chosen: new Set(), dirty: true,
    preferences: {min_count: 10, occasion_day: '', occasion_name: ''},
    loading: false, busy: false, error: '' });
}

async function loadEvents() {
  const request = ++E.request, root = S.root, mode = E.mode;
  E.loading = true;
  E.dirty = false;
  E.error = '';
  try {
    const result = await api('/api/events?mode=' + mode);
    if (request !== E.request || root !== S.root) return;
    if (result.root !== root) throw new Error('Открыта другая медиатека. Обновите страницу.');
    E.groups = result.groups;
    E.preferences = result.preferences;
    const keys = new Set(E.groups.map(g => g.id));
    E.chosen = new Set([...E.chosen].filter(k => keys.has(k)));
    if (!keys.has(E.active)) {
      E.active = null;
      if (S.view === 'events') { S.selected.clear(); S.focus = null; }
    }
  } catch (e) {
    if (request === E.request) E.error = e.message;
  } finally {
    if (request === E.request) {
      E.loading = false;
      if (S.view === 'events') render();
    }
  }
}

function eventPeriod(g) {
  if (g.start == null) return 'Дата съёмки неизвестна';
  const a = dayKey(g.start), b = dayKey(g.end);
  return a === b ? fmtDay(a) : `${fmtDay(a)} — ${fmtDay(b)}`;
}

function eventItems(g) {
  return g.ids.map(id => S.byId.get(id)).filter(it => it && passFilters(it));
}

function eventButton(action, label, extra = '') {
  return `<button class="btn small" data-act="event-${action}" ${extra} ${E.busy || E.loading ? 'disabled' : ''}>${label}</button>`;
}

function renderEvents() {
  if (E.dirty && !E.loading && !E.busy) loadEvents();
  const el = $('#view-events');
  el.lazyObs?.disconnect();
  el.lazy = null;
  let html = `<div class="event-toolbar" aria-label="Масштаб группировки">
    ${eventButton('mode', 'События', `data-mode="events" aria-pressed="${E.mode === 'events'}"`)}
    ${eventButton('mode', 'Поездки', `data-mode="trips" aria-pressed="${E.mode === 'trips'}"`)}
    <span class="spacer"></span>${eventButton('settings', 'Настроить…')}
    ${eventButton('refresh', E.loading ? 'Обновление…' : 'Обновить')}
    </div>`;
  S.list = [];
  if (E.error) {
    el.innerHTML = html + emptyHTML('Не удалось загрузить события', esc(E.error));
    return;
  }
  if (!E.groups) {
    el.innerHTML = html + emptyHTML('Собираем воспоминания…', 'Группируем снимки по времени и GPS.');
    return;
  }
  const active = E.groups.find(g => g.id === E.active);
  if (active) {
    const list = sortList(eventItems(active));
    S.list = list;
    const visibleIds = new Set(list.map(it => it.id));
    S.selected = new Set([...S.selected].filter(id => visibleIds.has(id)));
    if (!visibleIds.has(S.focus)) S.focus = null;
    updateSelInfo();
    $('#pageTitle').textContent = active.name;
    $('#pageDescription').textContent = `${eventPeriod(active)} · показано ${list.length} из ${active.ids.length}`;
    html += `<div class="event-toolbar">${eventButton('back', '← Все группы')}
      ${eventButton('rename', 'Переименовать')}
      ${eventButton('split', 'Выделенные → новое событие')}
      ${eventButton('move', 'Перенести выделенные…')}
      ${active.manual ? eventButton('reset', 'Вернуть автогруппировку…') : ''}</div>
      <p class="muted">Выделите снимки кликом, Ctrl или Shift; двойной клик открывает просмотр.
      Правки сохраняются только в режиме «${E.mode === 'trips' ? 'Поездки' : 'События'}».</p>`;
    html += list.length ? daysHTML(list, { lazy: newLazy(el) })
      : emptyHTML('Нет снимков по выбранным фильтрам', 'Измените условия в панели «Фильтры и вид».');
    el.innerHTML = html;
    attachLazy(el);
    return;
  }
  const eligible = E.groups.filter(g => g.ids.length >= E.preferences.min_count && g.start != null
    && (E.mode !== 'trips' || g.manual || !g.home_known || g.is_trip));
  const visible = eligible.map(g => ({ g, items: eventItems(g) })).filter(x => x.items.length);
  const visibleKeys = new Set(visible.map(x => x.g.id));
  E.chosen = new Set([...E.chosen].filter(k => visibleKeys.has(k)));
  html += `<p class="event-explanation">${E.mode === 'trips'
    ? 'Поездки целиком: дорога, отдых и возвращение. Названия стран и городов определяются по GPS. Если привычный район определить не удалось, границы поездок приблизительные.'
    : 'Крупные съёмки и выезды на несколько дней. Небольшие группы скрыты. В настройках можно указать свою памятную дату — например, день рождения.'}
    Показаны группы от ${E.preferences.min_count} снимков. Названия и границы можно исправить.</p>
    <div class="event-toolbar"><span class="muted">Групп: ${visible.length} · выбрано: ${E.chosen.size}</span>
    ${eventButton('merge', 'Объединить выбранные…', E.chosen.size < 2 ? 'disabled' : '')}
    ${E.chosen.size ? eventButton('clear', 'Снять выбор') : ''}</div>`;
  html += visible.length ? `<div class="event-cards">${visible.map(({ g, items }) => {
    const chosen = E.chosen.has(g.id);
    const photos = items.filter(it => it.c === 'photo' && it.st !== 2);
    const covers = photos.length ? photos : items;
    const cover = covers[Math.floor(covers.length / 2)];
    return `<article class="event-card ${chosen ? 'chosen' : ''}">
      <button class="event-cover" data-act="event-open" data-key="${g.id}" aria-label="Открыть ${esc(g.name)}">
        <img src="${thumbUrl(cover)}" alt="" loading="lazy" decoding="async"></button>
      <div class="event-card-body"><span class="event-origin">${g.manual ? 'Ваше событие' : g.occasion ? 'Около памятной даты' : 'Автоматически'} · ${g.gps ? 'По географии и времени' : 'По времени'}</span>
        <button class="event-title" data-act="event-open" data-key="${g.id}">${esc(g.name)}</button>
        <p>${eventPeriod(g)}</p><p>${items.length === g.ids.length ? plural(g.ids.length, 'файл', 'файла', 'файлов') : `Показано ${items.length} из ${g.ids.length}`}</p>
        ${eventButton('choose', chosen ? '✓ Выбрано' : 'Выбрать для объединения', `data-key="${g.id}" aria-pressed="${chosen}"`)}
      </div></article>`;
  }).join('')}</div>` : emptyHTML('Крупных групп не найдено', 'В настройках можно уменьшить минимальное число снимков. Все фотографии по-прежнему доступны в разделе «Все снимки».');
  el.innerHTML = html;
}

async function eventAction(action, el) {
  if (E.busy) return;
  const key = el.dataset.key;
  if (action === 'event-settings') {
    if (E.loading) return;
    const root = S.root, request = E.request, p = E.preferences;
    const [month, day] = (p.occasion_day || '-').split('-');
    const accepted = await confirmModal('Настройки событий и поездок', `
      <label>Минимум снимков в карточке <input id="eventMinCount" type="number" min="2" max="10000" value="${p.min_count}"></label>
      <h4>Памятная дата — необязательно</h4>
      <p class="muted">Своя дата для этой медиатеки. Выезды около неё получат предложенное название с годом. Это подсказка, а не распознавание праздника или отеля.</p>
      <label>Название <input id="eventOccasionName" class="event-name-input" maxlength="80" placeholder="Например, День рождения или Годовщина" value="${esc(p.occasion_name)}"></label>
      <div class="event-toolbar"><label>День <input id="eventOccasionDay" type="number" min="1" max="31" value="${day}"></label>
      <label>Месяц <input id="eventOccasionMonth" type="number" min="1" max="12" value="${month}"></label></div>
      <p class="muted">Оставьте день и месяц пустыми, чтобы отключить подсказку. Ручные названия сохранятся.</p>`, 'Сохранить');
    if (!accepted || root !== S.root || request !== E.request) return;
    const d = $('#eventOccasionDay').value, m = $('#eventOccasionMonth').value;
    const min_count = Number($('#eventMinCount').value);
    if (!Number.isInteger(min_count) || min_count < 2 || min_count > 10000 || Boolean(d) !== Boolean(m)) {
      toast('Проверьте количество снимков, день и месяц.', true); return;
    }
    E.busy = true;
    try {
      const result = await api('/api/event-settings', {root, min_count,
        occasion_day: d && m ? `${m.padStart(2, '0')}-${d.padStart(2, '0')}` : '',
        occasion_name: $('#eventOccasionName').value.trim()});
      if (root !== S.root || request !== E.request) return;
      E.preferences = result; E.dirty = true;
    } catch (e) { toast(esc(e.message), true); }
    finally { if (request === E.request) E.busy = false; }
  } else if (action === 'event-mode') {
    if (E.mode === el.dataset.mode) return;
    resetEvents(); E.mode = el.dataset.mode;
  } else if (action === 'event-refresh') { E.dirty = true; }
  else if (action === 'event-open') { E.active = key; }
  else if (action === 'event-back') { E.active = null; }
  else if (action === 'event-choose') {
    if (E.chosen.has(key)) E.chosen.delete(key); else E.chosen.add(key);
  } else if (action === 'event-clear') { E.chosen.clear(); }
  else {
    if (E.loading) { toast('Дождитесь обновления списка событий.'); return; }
    const operation = action.slice(6), group = E.groups?.find(g => g.id === E.active);
    const root = S.root, mode = E.mode, request = E.request;
    const groups = operation === 'merge' ? [...E.chosen] : group ? [group.id] : [];
    const ids = targetIds().filter(id => group?.ids.includes(id) && S.list.some(it => it.id === id));
    if (!groups.length) return;
    if (['split', 'move'].includes(operation) && !ids.length) {
      toast('Сначала выделите снимки внутри события.'); return;
    }
    if (operation === 'split' && ids.length === group.ids.length) {
      toast('Оставьте хотя бы один снимок в исходном событии.'); return;
    }
    let name = '', target = null;
    if (['rename', 'merge', 'split'].includes(operation)) {
      const title = { rename: 'Название события', merge: 'Объединить события', split: 'Новое событие из выделенных' }[operation];
      const html = `${operation === 'merge' ? '<p>Будут объединены все снимки выбранных групп, включая скрытые фильтрами.</p>' : ''}
        <label>Название <input id="eventName" class="event-name-input" maxlength="120" value="${esc(operation === 'rename' ? group.name : '')}" placeholder="Например, Поездка в горы"></label>`;
      const accepted = confirmModal(title, html, 'Сохранить');
      $('#eventName').focus();
      if (!await accepted) return;
      name = $('#eventName').value.trim();
      if (!name) { toast('Название не может быть пустым.'); return; }
    } else if (operation === 'move') {
      const options = E.groups.filter(g => g.id !== group.id);
      if (!options.length) { toast('Нет другого события. Можно создать новое из выделенных.'); return; }
      if (!await confirmModal('Перенести выделенные снимки', `<label>В событие <select id="eventTarget">${options.map(g =>
        `<option value="${g.id}">${esc(g.name)} · ${eventPeriod(g)}</option>`).join('')}</select></label>`, 'Перенести')) return;
      target = $('#eventTarget').value;
    } else if (operation === 'reset') {
      if (!await confirmModal('Вернуть автоматическую группировку?', '<p>Ручное название и состав этой группы будут сброшены. Снимки снова сгруппируются по времени и месту.</p>', 'Сбросить')) return;
    } else return;
    if (root !== S.root || mode !== E.mode || request !== E.request) {
      toast('Список событий обновился. Повторите действие.'); return;
    }
    E.busy = true;
    renderEvents();
    try {
      const result = await api('/api/events', { root, mode, action: operation, groups, ids, name, target });
      if (root !== S.root || mode !== E.mode || request !== E.request) return;
      E.groups = result.groups;
      E.active = result.active;
      E.chosen.clear();
      toast('Изменения события сохранены.');
    } catch (e) {
      toast('Не удалось сохранить: ' + esc(e.message), true);
      E.dirty = true;
    } finally {
      if (request === E.request) E.busy = false;
    }
  }
  S.selected.clear(); S.focus = null; S.anchor = null;
  if (S.view === 'events') { render(); refreshSelection(); }
}
