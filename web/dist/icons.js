(() => {
  'use strict';

  const namespace = 'http://www.w3.org/2000/svg';
  const path = (d, attributes = {}) => ['path', { d, ...attributes }];
  const line = (x1, y1, x2, y2) => ['line', { x1, y1, x2, y2 }];
  const circle = (cx, cy, r, attributes = {}) => ['circle', { cx, cy, r, ...attributes }];
  const filled = { fill: 'currentColor', stroke: 'none' };

  // Drawn on one 24px grid: open contours, restrained detail, and soft terminals.
  // The 15-second numerals are paths, avoiding differences between system fonts.
  const fifteen = [
    path('M8.1 10.5 9.3 9.7v5.8M8.1 15.5h2.4', { 'stroke-width': 1.1 }),
    path('M15.7 9.7h-3.4l-.2 2.7h1.7c1.2 0 2 .6 2 1.6s-.8 1.7-2 1.7c-.8 0-1.4-.2-1.9-.6', { 'stroke-width': 1.1 })
  ];
  const definitions = Object.freeze({
    'book-open': [
      path('M12 5.6C9.3 3.7 6 3.3 3 4.3v14.2c3-1 6.3-.6 9 1.3 2.7-1.9 6-2.3 9-1.3V4.3c-3-1-6.3-.6-9 1.3Z'),
      line(12, 5.6, 12, 19.8),
      path('M6 7.7c1.1 0 2.1.2 3 .6M15 8.3c.9-.4 1.9-.6 3-.6')
    ],
    follow: [
      path('M4 6h16M10 12h10M10 18h7'),
      path('m3.5 9 3 3-3 3'),
      line(3.5, 12, 6.5, 12)
    ],
    settings: [
      path('M3.5 19 9.1 5h1.8l5.6 14M5.5 14h9'),
      path('M18.2 10.2c3.8-1.3 3.6 2.3 3.6 4.4V19M21.8 14c-4.6-1-5 5 .1 3.8', { 'stroke-width': 1.35 })
    ],
    'sign-out': [
      path('M10.5 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5.5'),
      path('M9 12h11m-4-4 4 4-4 4')
    ],
    'arrow-right': [path('M4 12h15.5m-5.5-5.5 5.5 5.5-5.5 5.5')],
    'arrow-left': [path('M20 12H4.5m5.5-5.5L4.5 12l5.5 5.5')],
    play: [path('M8 5.9c0-.7.8-1.1 1.4-.7l9.2 6.1a.8.8 0 0 1 0 1.4l-9.2 6.1c-.6.4-1.4 0-1.4-.7Z', filled)],
    pause: [
      ['rect', { x: 6.5, y: 5, width: 3.5, height: 14, rx: .7, ...filled }],
      ['rect', { x: 14, y: 5, width: 3.5, height: 14, rx: .7, ...filled }]
    ],
    replay: [
      path('M4.7 7.8a8.2 8.2 0 1 1-.8 8.9M4.7 3.8v4h4'),
      path('m10.5 8.6 5 3.4-5 3.4Z', filled)
    ],
    'rewind-15': [
      path('M4.7 7.8a8.2 8.2 0 1 1-.8 8.9M4.7 3.8v4h4'),
      ...fifteen
    ],
    'forward-15': [
      path('M19.3 7.8a8.2 8.2 0 1 0 .8 8.9M19.3 3.8v4h-4'),
      ...fifteen
    ],
    artwork: [
      ['rect', { x: 3, y: 4.5, width: 18, height: 15, rx: 1.25 }],
      circle(8, 9, 1.5),
      path('m3 16.5 5.5-4 4.2 3.1 3.8-5.1 4.5 6')
    ],
    close: [path('m6.5 6.5 11 11m0-11-11 11')],
    retry: [
      path('M4.5 8a8.3 8.3 0 0 1 14.2-2.2L20 7M20 3v4h-4'),
      path('M19.5 16a8.3 8.3 0 0 1-14.2 2.2L4 17M4 21v-4h4')
    ],
    headphones: [
      path('M4 13v-1a8 8 0 0 1 16 0v1'),
      path('M4 12H3a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h2a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1Zm16 0h1a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-2a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1Z')
    ],
    'text-size': [
      path('m3 18 4-10 4 10M4.5 14.5h5M12 18l5-14 5 14M13.8 13h6.4')
    ],
    download: [
      path('M12 3.5v11m-4-4 4 4 4-4M4.5 15.5v4a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1v-4')
    ],
    'chevron-down': [path('m6.5 9.5 5.5 5 5.5-5')],
    check: [path('m5 12.5 4.5 4.5L19 7')],
    bookmark: [path('M6 3.5h12v17l-6-4-6 4Z')],
    restart: [
      line(4, 5, 4, 19),
      path('M19.5 12H8m5-5-5 5 5 5')
    ]
  });

  function create(name) {
    if (typeof name !== 'string' || !Object.prototype.hasOwnProperty.call(definitions, name)) return null;
    const svg = document.createElementNS(namespace, 'svg');
    const attributes = {
      class: 'lumen-icon',
      'data-icon': name,
      viewBox: '0 0 24 24',
      width: 24,
      height: 24,
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': 1.5,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'aria-hidden': 'true',
      focusable: 'false'
    };
    for (const [key, value] of Object.entries(attributes)) svg.setAttribute(key, value);
    for (const [tag, shapeAttributes] of definitions[name]) {
      const shape = document.createElementNS(namespace, tag);
      for (const [key, value] of Object.entries(shapeAttributes)) shape.setAttribute(key, value);
      svg.append(shape);
    }
    return svg;
  }

  function set(button, name, label) {
    const svg = create(name);
    if (!svg || !button || typeof button.replaceChildren !== 'function') return false;
    const text = document.createElement('span');
    text.className = 'control-label';
    text.textContent = label == null ? button.textContent.trim() : String(label);
    button.replaceChildren(svg, text);
    return true;
  }

  function hydrate(root = document) {
    const targets = Array.from(root.querySelectorAll('[data-lumen-icon]'));
    if (root.matches && root.matches('[data-lumen-icon]')) targets.unshift(root);
    for (const target of targets) {
      const svg = create(target.getAttribute('data-lumen-icon'));
      if (svg) target.replaceChildren(svg);
    }
  }

  window.LumenIcons = Object.freeze({ create, set, hydrate });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => hydrate(), { once: true });
  } else {
    hydrate();
  }
})();
