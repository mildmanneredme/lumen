/* Installation is offered in settings, only after the listener chooses it. */
(() => {
  'use strict';
  const section = document.getElementById('install-section');
  const button = document.getElementById('install-app');
  const status = document.getElementById('install-status');
  const standalone = window.matchMedia('(display-mode: standalone)');
  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isIOSChrome = isIOS && /CriOS/.test(navigator.userAgent);
  const isAndroid = /Android/.test(navigator.userAgent);
  let deferredPrompt = null;
  let installed = false;

  function isInstalled() {
    return installed || standalone.matches || navigator.standalone === true;
  }

  function instructions() {
    if (isIOSChrome) return 'In Chrome, tap Share beside the address bar, then Add to Home Screen → Add. If Open as Web App is shown, leave it enabled.';
    if (isIOS) return 'Tap Share, then Add to Home Screen. Leave Open as Web App enabled if shown, then tap Add. In Safari, Share may be inside the More menu.';
    if (isAndroid) return 'In Chrome, open the ⋮ menu, then Install and create shortcut → Install. Some versions show Add to Home screen → Install.';
    return 'In Chrome, use the install icon in the address bar, or open the ⋮ menu and choose Cast, save, and share → Install page as app. On a phone, open this same address in Chrome.';
  }

  function render(message) {
    if (!section || !button || !status) return;
    section.hidden = isInstalled();
    button.textContent = deferredPrompt ? 'Install Lumen' : 'How to install';
    if (message) status.textContent = message;
    else status.textContent = 'Add Lumen to your Home screen to open it as an app. Your place stays saved on this browser.';
  }

  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    deferredPrompt = event;
    render('Lumen is ready to install. Tap Install Lumen to add it to your Home screen.');
  });

  window.addEventListener('appinstalled', () => {
    installed = true;
    deferredPrompt = null;
    render();
  });

  if (standalone.addEventListener) standalone.addEventListener('change', () => render());
  else if (standalone.addListener) standalone.addListener(() => render());

  if (button) button.addEventListener('click', async () => {
    if (!deferredPrompt) {
      render(instructions());
      return;
    }
    // Consume this event once. A new browser event is required for another prompt.
    const prompt = deferredPrompt;
    deferredPrompt = null;
    button.disabled = true;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      if (choice.outcome === 'accepted') {
        installed = true;
        render();
      } else {
        deferredPrompt = null;
        render('You can install Lumen later from this button or the Chrome menu.');
      }
    } catch (_) {
      render(instructions());
    } finally {
      button.disabled = false;
    }
  });

  render();

  async function registerWorker() {
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
    try {
      const registration = await navigator.serviceWorker.register('/sw.js', {
        scope: '/',
        updateViaCache: 'none'
      });
      window.dispatchEvent(new CustomEvent('lumen:pwa-ready', {detail: {registration}}));
      function announceUpdate() {
        if (registration.waiting && navigator.serviceWorker.controller) {
          // An update waits for the reader to close. Never reload during narration.
          window.dispatchEvent(new CustomEvent('lumen:pwa-update', {detail: {registration}}));
        }
      }
      announceUpdate();
      registration.addEventListener('updatefound', () => {
        const worker = registration.installing;
        if (worker) worker.addEventListener('statechange', () => {
          if (worker.state === 'installed') announceUpdate();
        });
      });
    } catch (_) {
      // Registration/cache failure must not prevent listening or saving a bookmark.
      window.dispatchEvent(new CustomEvent('lumen:pwa-unavailable'));
    }
  }
  if (document.readyState === 'complete') registerWorker();
  else window.addEventListener('load', registerWorker, {once: true});
})();
