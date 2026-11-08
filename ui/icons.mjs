// Toolbar icons: 24×24 line drawings, stroked with the text colour.
export const ICONS = {
  open: '<path d="M3 7.5V18a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9.5a2 2 0 0 0-2-2h-7L10 5H5a2 2 0 0 0-2 2.5z"/>',
  save: '<path d="M5 4h11l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1z"/><path d="M8 4v5h7V4"/><rect x="8" y="13" width="8" height="7"/>',
  menu: '<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>',
  sidebar: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M9.5 4.5v15"/><path d="M5.5 8h2M5.5 11h2M5.5 14h2"/>',
  prev: '<path d="M15 6l-6 6 6 6"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  'zoom-out': '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5"/>',
  'zoom-in': '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5M10.5 8v5"/>',
  rotate: '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/>',
  'rotate-left': '<path d="M4 12a8 8 0 1 0 2.6-5.9"/><path d="M4 4v5h5"/>',
  'delete-pages': '<path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6"/>',
  'insert-pdf': '<path d="M13 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9.5z"/><path d="M13 3.5v6h6M12 12v6M9 15h6"/>',
  'undo-pages': '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  'tool-select': '<path d="M6 3.5l12 8.5-5.5 1.2L15.5 20l-2.6 1.2-3-6.8L6 18.5z"/>',
  'tool-highlight': '<path d="M14.5 4.5l5 5-8 8H6.5v-5z"/><path d="M4 21h16" stroke-width="3" opacity=".55"/>',
  'tool-note': '<path d="M4.5 4.5h15v11l-4 4h-11z"/><path d="M15.5 19.5v-4h4M8 9h8M8 12.5h5"/>',
  'tool-draw': '<path d="M4 20c3-1 4-6 7-6s2 4 5 4 3-3 4-4"/><path d="M16 3.5l4 4-6.5 6.5h-4v-4z"/>',
  ul: '<circle cx="5" cy="7" r="1"/><circle cx="5" cy="12" r="1"/><circle cx="5" cy="17" r="1"/><path d="M9.5 7H20M9.5 12H20M9.5 17H20"/>',
  ol: '<path d="M4 5h1.5v4M4 9h3M4 14.5c0-1 .8-1.5 1.5-1.5S7 13.5 7 14.3c0 1-3 2.2-3 3.7h3"/><path d="M10 7h10M10 12h10M10 17h10"/>',
  align: '<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>',
  'align-center': '<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>',
  'align-right': '<path d="M4 6h16M10 10h10M4 14h16M10 18h10"/>',
  'align-justify': '<path d="M4 6h16M4 10h16M4 14h16M4 18h16"/>',
  table: '<rect x="3.5" y="4.5" width="17" height="15" rx="1"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10M15 9.5v10"/>',
  image: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><circle cx="9" cy="9.5" r="1.6"/><path d="M4 17l5-4.5 4 3.5 3-2.5 4 3.5"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  'find-prev': '<path d="M6 15l6-6 6 6"/>',
  'find-next': '<path d="M6 9l6 6 6-6"/>',
  settings: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  export: '<path d="M12 15V3.5M8 7.5l4-4 4 4"/><path d="M5 12v7a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19v-7"/>',
  print: '<path d="M7 9V3.5h10V9"/><rect x="3.5" y="9" width="17" height="8" rx="1.5"/><path d="M7 14h10v6.5H7z"/>',
  'save-as': '<path d="M5 4h11l3 3v5"/><path d="M5 4v15a1 1 0 0 0 1 1h5"/><path d="M8 4v5h7V4"/><path d="M14 20l1-3 5-5 2 2-5 5z"/>',
  new: '<path d="M13 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9.5z"/><path d="M13 3.5v6h6"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
};

export const TEXT_ICONS = { bold: 'B', italic: 'I', underline: 'U' };

export function icon(name) {
  if (TEXT_ICONS[name]) {
    const style = name === 'italic' ? 'font-style:italic' : name === 'underline' ? 'text-decoration:underline' : '';
    return `<span class="txt" style="${style}">${TEXT_ICONS[name]}</span>`;
  }
  const body = ICONS[name];
  return body ? `<svg viewBox="0 0 24 24" aria-hidden="true">${body}</svg>` : '';
}
