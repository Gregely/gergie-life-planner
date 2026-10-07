'use strict';

// Meal planner PWA. Plain JS, no build step. All quantity maths (scaling,
// shopping list, pantry deduction) happens in the backend; this file only
// displays what the API returns and sends what you type.

// ---------------------------------------------------------------- helpers

/** Create an element. Props starting with "on" are event listeners. */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'class') el.className = value;
    else if (['value', 'checked', 'selected', 'disabled', 'hidden'].includes(key)) el[key] = value;
    else el.setAttribute(key, value === true ? '' : value);
  }
  return el;
}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch('/api' + path, {
      method,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("Can't reach the meal planner server.");
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new Error(errorText(data, res));
  return data;
}

function errorText(data, res) {
  const detail = data && data.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => {
      const where = (e.loc || []).filter((p) => p !== 'body' && p !== 'query').join(' › ');
      return where ? `${where}: ${e.msg}` : e.msg;
    }).join('; ');
  }
  return `${res.status} ${res.statusText}`;
}

let msgTimer;
function flash(text, isError = false) {
  const box = document.getElementById('msg');
  box.textContent = text;
  box.className = isError ? 'error' : '';
  box.hidden = false;
  clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { box.hidden = true; }, isError ? 6000 : 2500);
}

// Dates are handled as local calendar days, sent to the API as YYYY-MM-DD.
const pad = (n) => String(n).padStart(2, '0');
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseIso = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const mondayOf = (d) => addDays(d, -((d.getDay() + 6) % 7));
const validIso = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || '') && !Number.isNaN(parseIso(s).getTime());
const dayLabel = (d) => d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
const longDate = (d) => d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

// Display formatting only; the numbers themselves come from the backend.
const num = (n) => Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
const qty = (n, unit) => (unit === 'count' ? `${num(n)}` : `${num(n)} ${unit}`);
const money = (n) => Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const portionsText = (p) => `${num(p)} portion${Number(p) === 1 ? '' : 's'}`;

/** Parse a number input; '' -> null, otherwise a Number (NaN is passed on for the API to reject). */
function readNumber(input) {
  const raw = input.value.trim();
  return raw === '' ? null : Number(raw);
}

/**
 * Modal dialog. Each button may have an async `action`; on success the
 * dialog closes and the current view re-renders, on failure the error is
 * shown inside the dialog so nothing typed is lost.
 */
function openDialog(title, content, buttons) {
  const error = h('p', { class: 'error', hidden: true });
  const dlg = h('dialog', {},
    h('h3', {}, title),
    content,
    error,
    h('div', { class: 'actions' }, buttons.map((b) => h('button', {
      type: 'button',
      class: b.class,
      onclick: async (ev) => {
        if (!b.action) { dismiss(); return; }
        const button = ev.currentTarget; // currentTarget is null once we await
        button.disabled = true;
        try {
          await b.action();
          dismiss();
          render();
        } catch (e) {
          error.textContent = e.message;
          error.hidden = false;
        } finally {
          button.disabled = false;
        }
      },
    }, b.label))),
  );
  // Remove right away (the 'close' event fires a tick later) so a dialog opened
  // from an action never coexists with the one that opened it.
  const dismiss = () => { dlg.close(); dlg.remove(); };
  dlg.addEventListener('close', () => dlg.remove()); // Esc key
  document.body.append(dlg);
  dlg.showModal();
  return dlg;
}

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode etc. */ }
  },
};

/** Sort by category (uncategorised last), then name — matches the backend's shopping list order. */
function byCategoryThenName(a, b) {
  return (!a.category - !b.category)
    || (a.category || '').localeCompare(b.category || '', undefined, { sensitivity: 'base' })
    || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
}

function groupBy(items, keyFn) {
  const groups = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return groups;
}

// ---------------------------------------------------------------- plan

const SLOTS = ['breakfast', 'lunch', 'dinner'];
const SLOT_LABEL = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };

async function planView([week]) {
  const start = mondayOf(validIso(week) ? parseIso(week) : new Date());
  const end = addDays(start, 6);
  const [entries, recipes] = await Promise.all([
    api('GET', `/plan?start=${iso(start)}&end=${iso(end)}`),
    api('GET', '/recipes'),
  ]);
  const byCell = new Map(entries.map((e) => [`${e.date}|${e.slot}`, e]));
  const today = iso(new Date());

  const rows = [];
  for (let i = 0; i < 7; i++) {
    const day = addDays(start, i);
    const date = iso(day);
    rows.push(h('tr', { class: date === today ? 'today' : null },
      h('th', { scope: 'row' }, dayLabel(day)),
      SLOTS.map((slot) => {
        const entry = byCell.get(`${date}|${slot}`);
        return h('td', { class: entry && entry.eaten_at ? 'eaten' : null },
          h('button', {
            type: 'button',
            class: entry ? null : 'empty',
            'aria-label': `${SLOT_LABEL[slot]} ${longDate(day)}`,
            onclick: () => editPlanCell(date, slot, entry, recipes),
          }, entry
            ? [entry.recipe_name, h('br'), h('small', {}, entry.eaten_at ? '✓ eaten' : portionsText(entry.portions))]
            : '+'),
          entry && !entry.eaten_at ? h('button', {
            type: 'button',
            class: 'eat',
            'aria-label': `Mark ${SLOT_LABEL[slot].toLowerCase()} on ${longDate(day)} as eaten`,
            onclick: () => confirmEaten(entry),
          }, 'Mark eaten') : null,
        );
      }),
    ));
  }

  return [
    h('h2', {}, `Week of ${longDate(start)}`),
    h('div', { class: 'toolbar' },
      h('a', { class: 'button', href: `#/plan/${iso(addDays(start, -7))}`, 'aria-label': 'Previous week' }, '‹'),
      h('a', { class: 'button', href: '#/plan' }, 'This week'),
      h('a', { class: 'button', href: `#/plan/${iso(addDays(start, 7))}`, 'aria-label': 'Next week' }, '›'),
      h('span', { class: 'spacer' }),
      h('a', { class: 'button primary', href: `#/shopping/${iso(start)}/${iso(end)}` }, 'Shopping list'),
    ),
    recipes.length ? null : h('p', { class: 'muted' }, 'No recipes yet. ', h('a', { href: '#/recipes/new' }, 'Add one'), ' to start planning.'),
    h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, h('th', {}, ''), SLOTS.map((s) => h('th', { scope: 'col' }, SLOT_LABEL[s])))),
      h('tbody', {}, rows),
    ),
  ];
}

function editPlanCell(date, slot, entry, recipes) {
  const recipeSelect = h('select', { id: 'plan-recipe' },
    h('option', { value: '' }, '— choose a recipe —'),
    recipes.map((r) => h('option', { value: String(r.id) }, `${r.name} (makes ${r.servings})`)),
  );
  recipeSelect.value = entry ? String(entry.recipe_id) : '';
  const portions = h('input', { id: 'plan-portions', type: 'number', inputmode: 'decimal', min: '0', step: 'any', value: entry ? String(entry.portions) : '1' });

  const save = async () => {
    if (!recipeSelect.value) throw new Error('Choose a recipe.');
    const body = { date, slot, recipe_id: Number(recipeSelect.value), portions: readNumber(portions) };
    if (entry) await api('PUT', `/plan/${entry.id}`, body);
    else await api('POST', '/plan', body);
  };
  const buttons = [];
  if (entry) buttons.push({ label: 'Remove', class: 'danger left', action: () => api('DELETE', `/plan/${entry.id}`) });
  buttons.push({ label: 'Cancel' }, { label: 'Save', class: 'primary', action: save });

  const takenLog = h('div', {});
  if (entry && entry.eaten_at) {
    api('GET', `/plan/${entry.id}/eaten`).then((rows) => {
      if (rows.length) takenLog.replaceChildren(takenList(rows.map((r) => ({ ...r, remaining: null }))));
    }).catch(() => { /* informational only */ });
  }

  openDialog(`${SLOT_LABEL[slot]}, ${longDate(parseIso(date))}`, [
    entry && entry.eaten_at ? [h('p', { class: 'muted' }, 'Already marked as eaten.'), takenLog] : null,
    h('label', { for: 'plan-recipe' }, 'Recipe'), recipeSelect,
    h('label', { for: 'plan-portions' }, 'Portions eaten'), portions,
    recipes.length ? null : h('p', { class: 'muted' }, 'No recipes yet. Add one on the Recipes tab.'),
  ], buttons);
}

function confirmEaten(entry) {
  openDialog(`Mark ${entry.recipe_name} as eaten?`, [
    h('p', {}, `${SLOT_LABEL[entry.slot]}, ${longDate(parseIso(entry.date))}: ${portionsText(entry.portions)}.`),
    h('p', { class: 'muted' }, 'Its ingredients will be taken out of the pantry.'),
  ], [
    { label: 'Cancel' },
    {
      label: 'Mark eaten',
      class: 'primary',
      action: async () => showEatenResult(await api('POST', `/plan/${entry.id}/eaten`)),
    },
  ]);
}

/** <details> listing what came out of the pantry (deductions or consumption-log rows). */
function takenList(rows) {
  const taken = rows.filter((r) => r.deducted > 0);
  if (!taken.length) return h('p', { class: 'muted' }, 'Nothing was taken from the pantry.');
  return h('details', {},
    h('summary', {}, 'Taken from the pantry'),
    h('ul', {}, taken.map((r) => h('li', {},
      `${r.name}: ${qty(r.deducted, r.unit)}`,
      r.remaining == null ? null : h('small', {}, ` (${qty(r.remaining, r.unit)} left)`)))),
  );
}

function showEatenResult(result) {
  // Staples (salt, oil...) are rarely counted precisely, so running short of
  // them is normal and is mentioned quietly. Real shortfalls are highlighted.
  const short = result.shortfalls.filter((s) => !s.is_staple);
  const staples = result.shortfalls.filter((s) => s.is_staple);
  openDialog(`${result.recipe_name}: eaten`, [
    short.length
      ? h('div', { class: 'warn' },
        h('p', {}, h('strong', {}, `The pantry ran short of ${short.length === 1 ? '1 ingredient' : `${short.length} ingredients`}:`)),
        h('ul', {}, short.map((s) => h('li', {},
          h('strong', {}, s.name), ` needed ${qty(s.required, s.unit)}, had ${qty(s.available, s.unit)}: `,
          h('strong', {}, `${qty(s.shortfall, s.unit)} short`)))),
        h('p', { class: 'muted' }, 'These are now at 0 in the pantry. If you actually had more, correct it on the Pantry tab.'))
      : h('p', {}, '✓ Pantry updated. Nothing ran short.'),
    staples.length
      ? h('p', { class: 'muted' }, `Staples not fully in the pantry: ${staples.map((s) => s.name).join(', ')}. No action needed.`)
      : null,
    takenList(result.deductions),
  ], [{ label: 'OK', class: 'primary' }]);
}

// ---------------------------------------------------------------- shopping

async function shoppingView([startParam, endParam]) {
  const thisMonday = mondayOf(new Date());
  const start = validIso(startParam) ? startParam : iso(thisMonday);
  const end = validIso(endParam) ? endParam : iso(addDays(parseIso(start), 6));
  const list = await api('GET', `/shopping-list?start=${start}&end=${end}`);

  // Checked items, per date range: ingredient id -> edited amount ('' = use the to-buy amount).
  const checkKey = `shopping-checked:${start}:${end}`;
  const saved = store.get(checkKey, {});
  const checked = new Map(Array.isArray(saved) // stage-2 format was a plain id list
    ? saved.map((id) => [id, ''])
    : Object.entries(saved).map(([id, amount]) => [Number(id), String(amount ?? '')]));
  const onList = new Set(list.items.map((i) => i.ingredient_id));
  for (const id of checked.keys()) if (!onList.has(id)) checked.delete(id);
  const persist = () => { store.set(checkKey, Object.fromEntries(checked)); updateActions(); };

  const startInput = h('input', { type: 'date', value: start, 'aria-label': 'From' });
  const endInput = h('input', { type: 'date', value: end, 'aria-label': 'To' });
  const go = () => { if (startInput.value && endInput.value) location.hash = `#/shopping/${startInput.value}/${endInput.value}`; };
  startInput.addEventListener('change', go);
  endInput.addEventListener('change', go);

  const groups = groupBy(list.items, (item) => item.category || 'Other');
  const sections = [...groups].map(([category, items]) => [
    h('h3', {}, category),
    h('ul', { class: 'list' }, items.map((item) => {
      const id = item.ingredient_id;
      const li = h('li', { class: checked.has(id) ? 'done' : null });
      const amount = h('input', {
        type: 'number', inputmode: 'decimal', min: '0', step: 'any',
        'aria-label': `${item.name} amount bought`,
        // Not edited -> show the to-buy amount; the backend uses its exact figure.
        value: checked.get(id) || String(+item.to_buy.toFixed(2)),
        oninput: () => { checked.set(id, amount.value); persist(); },
      });
      const boughtRow = h('div', { class: 'bought-row', hidden: !checked.has(id) },
        h('span', { class: 'muted' }, 'Bought'), amount, h('span', { class: 'muted' }, item.unit));
      const box = h('input', {
        type: 'checkbox',
        checked: checked.has(id),
        onchange: () => {
          if (box.checked) checked.set(id, ''); else checked.delete(id);
          amount.value = String(+item.to_buy.toFixed(2));
          li.classList.toggle('done', box.checked);
          boughtRow.hidden = !box.checked;
          persist();
        },
      });
      li.append(
        h('label', { class: 'check' },
          box,
          h('span', { class: 'grow' },
            h('span', { class: 'item-title' }, item.name), h('br'),
            h('small', {}, `need ${qty(item.needed, item.unit)}, have ${qty(item.in_pantry, item.unit)}`),
          ),
          h('span', { class: 'qty' }, qty(item.to_buy, item.unit),
            item.estimated_cost == null ? null : [h('br'), h('small', {}, money(item.estimated_cost))]),
        ),
        boughtRow,
      );
      return li;
    })),
  ]);

  const addButton = h('button', { type: 'button', class: 'primary', onclick: () => addCheckedToPantry() });
  const uncheckButton = h('button', { type: 'button', onclick: () => { store.set(checkKey, {}); render(); } }, 'Uncheck all');
  const actions = h('div', { class: 'toolbar bought-actions' }, addButton, uncheckButton);
  function updateActions() {
    actions.hidden = checked.size === 0;
    addButton.textContent = `Add ${checked.size} checked to pantry`;
  }
  updateActions();

  async function addCheckedToPantry() {
    const items = [...checked].map(([ingredient_id, edited]) => ({
      ingredient_id,
      quantity: edited.trim() === '' ? null : Number(edited),
    }));
    addButton.disabled = true;
    try {
      const result = await api('POST', '/shopping-list/bought', { start, end, items });
      for (const { ingredient_id } of result.added) checked.delete(ingredient_id);
      store.set(checkKey, Object.fromEntries(checked));
      flash(`Added ${result.added.length} item${result.added.length === 1 ? '' : 's'} to the pantry.`);
      render();
    } catch (e) {
      flash(e.message, true);
      addButton.disabled = false;
    }
  }

  return [
    h('h2', {}, 'Shopping list'),
    h('div', { class: 'row' }, h('div', { class: 'grow' }, startInput), '→', h('div', { class: 'grow' }, endInput)),
    h('p', { class: 'muted' }, 'Planned meals not yet eaten, minus the pantry. Staples are left off. Tick what you bought, then add it to the pantry.'),
    list.items.length ? sections : h('p', {}, 'Nothing to buy for these dates.'),
    list.items.length ? h('p', { class: 'total' },
      `Estimated total: ${money(list.estimated_total)}`,
      list.unpriced_items ? h('small', {}, ` (+ ${list.unpriced_items} item${list.unpriced_items === 1 ? '' : 's'} without a price)`) : null,
    ) : null,
    actions,
  ];
}

// ---------------------------------------------------------------- recipes

async function recipesView([id]) {
  if (id) return recipeEditor(id);
  const recipes = await api('GET', '/recipes');
  return [
    h('div', { class: 'toolbar' }, h('h2', {}, 'Recipes'), h('span', { class: 'spacer' }),
      h('a', { class: 'button primary', href: '#/recipes/new' }, 'New recipe')),
    recipes.length
      ? h('ul', { class: 'list' }, recipes.map((r) => h('li', { class: 'link', onclick: () => { location.hash = `#/recipes/${r.id}`; } },
        h('a', { href: `#/recipes/${r.id}`, class: 'item-title' }, r.name), h('span', { class: 'tag' }, `makes ${r.servings}`))))
      : h('p', { class: 'muted' }, 'No recipes yet.'),
  ];
}

async function recipeEditor(id) {
  const isNew = id === 'new';
  const [ingredients, recipe] = await Promise.all([
    api('GET', '/ingredients'),
    isNew ? Promise.resolve({ name: '', servings: 2, items: [] }) : api('GET', `/recipes/${id}`),
  ]);
  const unitOf = new Map(ingredients.map((i) => [String(i.id), i.unit]));

  const name = h('input', { id: 'recipe-name', value: recipe.name, autocomplete: 'off' });
  const servings = h('input', { id: 'recipe-servings', type: 'number', inputmode: 'numeric', min: '1', step: '1', value: String(recipe.servings) });
  const rows = h('div', {});

  const addRow = (item) => {
    const select = h('select', { 'aria-label': 'Ingredient' },
      h('option', { value: '' }, '— ingredient —'),
      ingredients.map((i) => h('option', { value: String(i.id) }, i.name)));
    select.value = item ? String(item.ingredient_id) : '';
    const unit = h('span', { class: 'unit' }, item ? item.unit : '');
    select.addEventListener('change', () => { unit.textContent = unitOf.get(select.value) || ''; });
    const amount = h('input', { type: 'number', inputmode: 'decimal', min: '0', step: 'any', 'aria-label': 'Amount', value: item ? String(item.quantity) : '' });
    const row = h('div', { class: 'item-row' }, select, amount, unit,
      h('button', { type: 'button', class: 'icon', 'aria-label': 'Remove ingredient', onclick: () => row.remove() }, '✕'));
    rows.append(row);
  };
  recipe.items.forEach(addRow);
  if (isNew) addRow();

  const save = async () => {
    const items = [...rows.children]
      .map((row) => ({ select: row.querySelector('select'), amount: row.querySelector('input') }))
      .filter(({ select, amount }) => select.value || amount.value.trim())
      .map(({ select, amount }) => {
        if (!select.value) throw new Error('Choose an ingredient for every amount you entered.');
        return { ingredient_id: Number(select.value), quantity: readNumber(amount) };
      });
    const body = { name: name.value, servings: readNumber(servings), items };
    try {
      if (isNew) await api('POST', '/recipes', body);
      else await api('PUT', `/recipes/${id}`, body);
    } catch (e) { flash(e.message, true); return; }
    flash('Recipe saved.');
    location.hash = '#/recipes';
  };
  const remove = async () => {
    if (!confirm(`Delete "${recipe.name}"?`)) return;
    try { await api('DELETE', `/recipes/${id}`); } catch (e) { flash(e.message, true); return; }
    flash('Recipe deleted.');
    location.hash = '#/recipes';
  };

  return [
    h('h2', {}, isNew ? 'New recipe' : 'Edit recipe'),
    h('label', { for: 'recipe-name' }, 'Name'), name,
    h('label', { for: 'recipe-servings' }, 'Servings (portions one batch makes)'), servings,
    h('h3', {}, 'Ingredients for one batch'),
    ingredients.length ? null : h('p', { class: 'muted' }, 'No ingredients yet. ', h('a', { href: '#/ingredients' }, 'Add some first'), '.'),
    rows,
    h('button', { type: 'button', onclick: () => addRow() }, '+ Add ingredient'),
    h('div', { class: 'toolbar', style: 'margin-top:20px' },
      isNew ? null : h('button', { type: 'button', class: 'danger', onclick: remove }, 'Delete'),
      h('span', { class: 'spacer' }),
      h('a', { class: 'button', href: '#/recipes' }, 'Cancel'),
      h('button', { type: 'button', class: 'primary', onclick: save }, 'Save'),
    ),
  ];
}

// ---------------------------------------------------------------- ingredients

const UNIT_LABEL = { g: 'grams (g)', ml: 'millilitres (ml)', count: 'count (items)' };

async function ingredientsView() {
  const ingredients = await api('GET', '/ingredients');
  const categories = [...new Set(ingredients.map((i) => i.category).filter(Boolean))].sort();
  const groups = groupBy([...ingredients].sort(byCategoryThenName), (i) => i.category || 'Other');
  return [
    h('div', { class: 'toolbar' }, h('h2', {}, 'Ingredients'), h('span', { class: 'spacer' }),
      h('button', { type: 'button', class: 'primary', onclick: () => editIngredient(null, categories) }, 'New ingredient')),
    ingredients.length ? [...groups].map(([category, items]) => [
      h('h3', {}, category),
      h('ul', { class: 'list' }, items.map((i) => h('li', { class: 'link', onclick: () => editIngredient(i, categories) },
        h('span', { class: 'item-title' }, i.name),
        h('span', { class: 'tag' }, i.unit),
        i.is_staple ? h('span', { class: 'tag' }, 'staple') : null,
        i.price_per_unit == null ? null : h('small', {}, ` ${i.price_per_unit} per ${i.unit === 'count' ? 'item' : i.unit}`),
      ))),
    ]) : h('p', { class: 'muted' }, 'No ingredients yet.'),
  ];
}

function editIngredient(ingredient, categories) {
  const i = ingredient || { name: '', unit: 'g', price_per_unit: null, is_staple: false, category: null };
  const name = h('input', { id: 'ing-name', value: i.name, autocomplete: 'off' });
  const unit = h('select', { id: 'ing-unit' }, Object.entries(UNIT_LABEL).map(([v, label]) => h('option', { value: v }, label)));
  unit.value = i.unit;
  const price = h('input', { id: 'ing-price', type: 'number', inputmode: 'decimal', min: '0', step: 'any', value: i.price_per_unit == null ? '' : String(i.price_per_unit) });
  const priceLabel = h('label', { for: 'ing-price' });
  const updatePriceLabel = () => { priceLabel.textContent = `Price per ${unit.value === 'count' ? 'item' : unit.value} (optional)`; };
  unit.addEventListener('change', updatePriceLabel);
  updatePriceLabel();
  const category = h('input', { id: 'ing-category', list: 'ing-categories', value: i.category || '', autocomplete: 'off', placeholder: 'e.g. Produce, Dairy' });
  const staple = h('input', { id: 'ing-staple', type: 'checkbox', checked: i.is_staple });

  const save = async () => {
    const body = { name: name.value, unit: unit.value, price_per_unit: readNumber(price), is_staple: staple.checked, category: category.value };
    if (ingredient) await api('PUT', `/ingredients/${ingredient.id}`, body);
    else await api('POST', '/ingredients', body);
  };
  const buttons = [];
  if (ingredient) {
    buttons.push({
      label: 'Delete', class: 'danger left',
      action: async () => { if (confirm(`Delete "${ingredient.name}"?`)) await api('DELETE', `/ingredients/${ingredient.id}`); },
    });
  }
  buttons.push({ label: 'Cancel' }, { label: 'Save', class: 'primary', action: save });

  openDialog(ingredient ? 'Edit ingredient' : 'New ingredient', [
    h('label', { for: 'ing-name' }, 'Name'), name,
    h('label', { for: 'ing-unit' }, 'Base unit (recipes and pantry use this)'), unit,
    priceLabel, price,
    h('label', { for: 'ing-category' }, 'Category (optional, groups the shopping list)'), category,
    h('datalist', { id: 'ing-categories' }, categories.map((c) => h('option', { value: c }))),
    h('label', { class: 'inline', for: 'ing-staple' }, staple, 'Staple (never on the shopping list)'),
  ], buttons);
}

// ---------------------------------------------------------------- pantry

async function pantryView() {
  const [ingredients, pantry] = await Promise.all([api('GET', '/ingredients'), api('GET', '/pantry')]);
  const stocked = new Map(pantry.map((p) => [p.ingredient_id, p.quantity]));
  const filter = h('input', { type: 'search', placeholder: 'Filter…', 'aria-label': 'Filter ingredients' });
  const sorted = [...ingredients].sort(byCategoryThenName);
  const list = h('div', {});

  for (const [category, items] of groupBy(sorted, (i) => i.category || 'Other')) {
    const section = h('section', {}, h('h3', {}, category));
    const ul = h('ul', { class: 'list' });
    for (const i of items) {
      const status = h('span', { class: 'saved', 'aria-live': 'polite' });
      const input = h('input', {
        type: 'number', inputmode: 'decimal', min: '0', step: 'any', 'aria-label': `${i.name} quantity`,
        placeholder: '—', value: stocked.has(i.id) ? String(stocked.get(i.id)) : '',
      });
      input.addEventListener('change', async () => {
        status.textContent = '…';
        try {
          const quantity = readNumber(input);
          if (quantity == null) {
            if (stocked.has(i.id)) await api('DELETE', `/pantry/${i.id}`);
            stocked.delete(i.id);
          } else {
            const saved = await api('PUT', `/pantry/${i.id}`, { quantity });
            stocked.set(i.id, saved.quantity);
          }
          status.textContent = '✓';
        } catch (e) {
          status.textContent = '';
          flash(`${i.name}: ${e.message}`, true);
        }
      });
      const li = h('li', { 'data-name': i.name.toLowerCase() },
        h('div', { class: 'pantry-row' },
          h('span', {}, h('span', { class: 'item-title' }, i.name), i.is_staple ? h('span', { class: 'tag' }, 'staple') : null),
          input,
          h('span', { class: 'muted' }, i.unit),
          status,
        ));
      ul.append(li);
    }
    section.append(ul);
    list.append(section);
  }

  filter.addEventListener('input', () => {
    const term = filter.value.trim().toLowerCase();
    for (const section of list.children) {
      let visible = 0;
      for (const li of section.querySelectorAll('li')) {
        li.hidden = !li.dataset.name.includes(term);
        if (!li.hidden) visible++;
      }
      section.hidden = visible === 0;
    }
  });

  return [
    h('h2', {}, 'Pantry'),
    ingredients.length
      ? [h('p', { class: 'muted' }, 'What you have now, in each ingredient\'s base unit. Leave blank if you have none.'), filter, list]
      : h('p', { class: 'muted' }, 'No ingredients yet. ', h('a', { href: '#/ingredients' }, 'Add some first'), '.'),
  ];
}

// ---------------------------------------------------------------- AI chat
// The API key lives only on the server. This screen talks to /api/ai/*; every
// change the AI wants to make arrives as a proposal card you apply or reject.

const PROPOSAL_STATUS = { applied: '✓ Applied', rejected: 'Rejected', discarded: 'Discarded' };

function proposalCard(proposal) {
  let p = proposal;
  const card = h('div', {});
  const error = h('p', { class: 'error', hidden: true });

  async function decide(action, button) {
    button.disabled = true;
    error.hidden = true;
    try {
      const res = await api('POST', `/ai/proposals/${p.id}/${action}`);
      p = res.proposal;
      draw();
      if (action === 'apply') {
        if (p.tool === 'mark_eaten') showEatenResult(res.result);
        else if (p.tool === 'record_bought') flash(`Added ${res.result.added.length} item${res.result.added.length === 1 ? '' : 's'} to the pantry.`);
        else flash('Applied.');
      }
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
      button.disabled = false;
    }
  }

  function draw() {
    const pending = p.status === 'pending';
    card.className = `proposal ${p.status}`;
    card.replaceChildren(
      h('div', { class: 'proposal-title' },
        p.tool === 'create_recipe' && pending ? h('span', { class: 'tag' }, 'draft') : null,
        ` ${p.summary}`),
      p.details.length ? h('ul', {}, p.details.map((d) => h('li', {}, d))) : null,
      pending
        ? h('div', { class: 'toolbar' },
          h('button', { type: 'button', class: 'primary', onclick: (ev) => decide('apply', ev.currentTarget) }, 'Apply'),
          h('button', { type: 'button', onclick: (ev) => decide('reject', ev.currentTarget) }, 'Reject'))
        : h('p', { class: 'muted' }, PROPOSAL_STATUS[p.status] || p.status),
      error,
    );
  }
  draw();
  return card;
}

function chatTurn(userText, reply, proposals) {
  return [
    h('div', { class: 'bubble user' }, userText),
    reply == null ? null : h('div', { class: 'bubble ai' }, reply),
    proposals.map(proposalCard),
  ];
}

async function chatView() {
  const status = await api('GET', '/ai/status');
  let sessionId = store.get('ai-session', null);
  let turns = [];
  if (sessionId) {
    try {
      turns = (await api('GET', `/ai/sessions/${sessionId}`)).turns;
    } catch {
      sessionId = null; // e.g. the database was restored; start fresh
      store.set('ai-session', null);
    }
  }

  const log = h('div', { class: 'chat-log', 'aria-live': 'polite' },
    turns.map((t) => chatTurn(t.user, t.reply, t.proposals)));
  const input = h('textarea', {
    rows: '3', maxlength: String(status.max_message_chars), 'aria-label': 'Message',
    placeholder: status.available ? 'e.g. "Plan chilli for dinner Mon–Thu, 1 portion each"' : 'AI unavailable',
    disabled: !status.available,
  });
  const sendButton = h('button', { type: 'button', class: 'primary', disabled: !status.available, onclick: () => send() }, 'Send');
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) send();
  });

  async function send() {
    const message = input.value.trim();
    if (!message || sendButton.disabled) return;
    sendButton.disabled = true;
    const userBubble = h('div', { class: 'bubble user' }, message);
    const waiting = h('div', { class: 'bubble ai muted' }, 'Thinking…');
    log.append(userBubble, waiting);
    waiting.scrollIntoView({ block: 'end' });
    try {
      const res = await api('POST', '/ai/chat', { session_id: sessionId, message });
      sessionId = res.session_id;
      store.set('ai-session', sessionId);
      waiting.remove();
      userBubble.remove();
      log.append(...chatTurn(message, res.reply, res.proposals).flat().filter(Boolean));
      input.value = '';
    } catch (e) {
      waiting.className = 'bubble ai error';
      waiting.textContent = e.message; // message stays in the box so you can retry
    } finally {
      sendButton.disabled = !status.available;
      log.lastElementChild?.scrollIntoView({ block: 'end' });
    }
  }

  const newChat = () => { store.set('ai-session', null); render(); };

  return [
    h('div', { class: 'toolbar' }, h('h2', {}, 'Chat'), h('span', { class: 'spacer' }),
      h('button', { type: 'button', onclick: newChat }, 'New chat')),
    status.available
      ? h('p', { class: 'muted' }, 'Ask about your plan, pantry or shopping. Changes only happen when you tap Apply.')
      : h('div', { class: 'warn' },
        h('p', {}, h('strong', {}, 'AI unavailable')),
        h('p', {}, status.reason),
        h('p', { class: 'muted' }, 'Everything else in the app works without it. Proposals already here can still be applied or rejected.')),
    log,
    h('div', { class: 'chat-input' }, input, sendButton),
  ];
}

// ---------------------------------------------------------------- router

const ROUTES = { plan: planView, shopping: shoppingView, recipes: recipesView, ingredients: ingredientsView, pantry: pantryView, chat: chatView };
let renderSeq = 0;

async function render() {
  const [route, ...params] = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  const name = ROUTES[route] ? route : 'plan';
  for (const link of document.querySelectorAll('#nav a')) link.classList.toggle('active', link.dataset.route === name);

  const seq = ++renderSeq;
  const main = document.getElementById('main');
  try {
    const content = await ROUTES[name](params.map(decodeURIComponent));
    if (seq !== renderSeq) return; // a newer navigation won
    main.replaceChildren(...[content].flat(Infinity).filter((n) => n != null && n !== false));
  } catch (e) {
    if (seq !== renderSeq) return;
    main.replaceChildren(h('p', { class: 'error' }, e.message), h('button', { type: 'button', onclick: render }, 'Retry'));
  }
}

window.addEventListener('hashchange', () => { render(); window.scrollTo(0, 0); });
render();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => { /* not fatal: app still works without it */ });
}
