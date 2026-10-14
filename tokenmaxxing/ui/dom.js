// Every source value becomes a text node or a form value. No HTML parser sinks.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else if (key === 'value' || key === 'checked' || key === 'disabled' || key === 'required') node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
export const button = (title, action, attrs = {}) => el('button', {type: 'button', onclick: action, ...attrs}, title);
export const paragraph = (text, cls = 'muted small') => el('p', {class: cls}, text);
export const badge = (text, cls = '') => el('span', {class: 'badge ' + cls}, text);
export const panel = (title, ...children) => el('section', {class: 'panel'}, el('h2', {}, title), ...children);
export const empty = text => el('div', {class: 'empty'}, text);
export const field = (title, control, help) => el('label', {}, title, control, help ? el('span', {class: 'help'}, help) : null);
export const input = (name, attrs = {}) => el('input', {name, autocomplete: 'off', ...attrs});
export const json = value => el('pre', {}, JSON.stringify(value, null, 2));
export const details = (title, ...children) => el('details', {}, el('summary', {}, title), ...children);
export const option = (value, title) => el('option', {value}, title);
export function select(name, choices, value, action) {
  const node = el('select', {name, ...(action ? {onchange: action} : {})}, choices.map(([v, t]) => option(v, t)));
  node.value = value;
  return node;
}
export function pager(page, action) {
  return el('div', {class: 'pager'},
    button('Previous', () => action(Math.max(0, page.offset - page.limit)), {disabled: page.offset === 0, 'data-page': 'previous'}),
    el('span', {}, page.total ? `${page.offset + 1}–${Math.min(page.offset + page.limit, page.total)} of ${page.total}` : '0 records'),
    button('Next', () => action(page.offset + page.limit), {disabled: page.offset + page.limit >= page.total, 'data-page': 'next'}));
}
export function form(id, action, ...children) {
  const node = el('form', {id, class: 'form-grid', onsubmit: event => {
    event.preventDefault();
    action(Object.fromEntries(new FormData(node)), node);
  }}, ...children);
  return node;
}
export const submit = title => el('button', {type: 'submit', class: 'primary'}, title);
export function preview(row) {
  const body = row.body;
  const content = body.canonical.content;
  if (typeof content === 'string') return content.slice(0, 180);
  if (Array.isArray(content)) {
    const text = content.filter(b => b && typeof b.text === 'string').map(b => b.text).join(' ');
    if (text) return text.slice(0, 180);
  }
  return `${body.kind} · ${row.category} · ${body.diagnostics.length} record diagnostics`;
}
