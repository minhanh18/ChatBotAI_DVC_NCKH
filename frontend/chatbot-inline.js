(function () {
  const chatFab = document.getElementById('chatFab');
  const chatPanel = document.getElementById('chatPanel');
  const chatClose = document.getElementById('chatClose');
  const chatFullscreen = document.getElementById('chatFullscreen');
  const chatFrame = document.getElementById('chatFrame');
  const chatFrameLoading = document.getElementById('chatFrameLoading');

  if (!chatFab || !chatPanel || !chatClose || !chatFullscreen || !chatFrame) return;

  const FAB_KEY = 'chatFabPos_v30';
  const FAB_SIZE = 56;
  const VIEW_MARGIN = 14;
  const PANEL_GAP = 10;
  const PANEL_MIN_TOP = 12;
  let hasLoadedEmbed = false;
  let isDragging = false;
  let dragStarted = false;
  let dragJustEnded = false;
  let startX = 0;
  let startY = 0;
  let startLeft = 0;
  let startTop = 0;

  const FULLSCREEN_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5"></path><path d="M20 9V4h-5"></path><path d="M4 15v5h5"></path><path d="M20 15v5h-5"></path></svg>';
  const RESTORE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4H4v4"></path><path d="M16 4h4v4"></path><path d="M8 20H4v-4"></path><path d="M16 20h4v-4"></path></svg>';

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function showLoader() {
    if (chatFrameLoading) chatFrameLoading.hidden = false;
  }

  function hideLoader() {
    if (chatFrameLoading) chatFrameLoading.hidden = true;
  }

  function ensureFrameLoaded() {
    const desiredSrc = '/assistant?embed=1';
    if (!hasLoadedEmbed || chatFrame.getAttribute('src') !== desiredSrc) {
      showLoader();
      chatFrame.setAttribute('src', desiredSrc);
      hasLoadedEmbed = true;
    }
  }

  function postHostMode(fullscreen) {
    try {
      chatFrame.contentWindow?.postMessage({ type: 'chatbot_host_mode', fullscreen: Boolean(fullscreen) }, '*');
    } catch (_) {
      // ignore
    }
  }

  function updateFullscreenButton(fullscreen) {
    if (fullscreen) {
      chatFullscreen.innerHTML = RESTORE_ICON;
      chatFullscreen.setAttribute('aria-pressed', 'true');
      chatFullscreen.setAttribute('title', 'Thu nhỏ lại');
      chatFullscreen.setAttribute('aria-label', 'Thu nhỏ lại');
    } else {
      chatFullscreen.innerHTML = FULLSCREEN_ICON;
      chatFullscreen.setAttribute('aria-pressed', 'false');
      chatFullscreen.setAttribute('title', 'Mở toàn màn hình');
      chatFullscreen.setAttribute('aria-label', 'Mở toàn màn hình');
    }
  }

  function saveFabPosition(x, y) {
    try {
      localStorage.setItem(FAB_KEY, JSON.stringify({ x, y }));
    } catch (_) {
      // ignore
    }
  }

  function loadFabPosition() {
    try {
      const raw = localStorage.getItem(FAB_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function measurePanel() {
    const wasOpen = chatPanel.classList.contains('open');
    const oldVisibility = chatPanel.style.visibility;
    if (!wasOpen) {
      chatPanel.classList.add('open');
      chatPanel.style.visibility = 'hidden';
    }
    const rect = {
      width: chatPanel.offsetWidth || 420,
      height: chatPanel.offsetHeight || 640,
    };
    if (!wasOpen) {
      chatPanel.classList.remove('open');
      chatPanel.style.visibility = oldVisibility;
    }
    return rect;
  }

  function syncPanelToFab() {
    if (chatPanel.classList.contains('fullscreen')) return;
    const fabRect = chatFab.getBoundingClientRect();
    const panelRect = measurePanel();

    let panelLeft = fabRect.left + fabRect.width - panelRect.width;
    panelLeft = clamp(panelLeft, VIEW_MARGIN, window.innerWidth - panelRect.width - VIEW_MARGIN);

    let panelTop = fabRect.top - panelRect.height - PANEL_GAP;
    if (panelTop < PANEL_MIN_TOP) {
      panelTop = clamp(fabRect.bottom + PANEL_GAP, PANEL_MIN_TOP, window.innerHeight - panelRect.height - VIEW_MARGIN);
    }
    panelTop = clamp(panelTop, PANEL_MIN_TOP, window.innerHeight - panelRect.height - VIEW_MARGIN);

    chatPanel.style.left = `${Math.round(panelLeft)}px`;
    chatPanel.style.top = `${Math.round(panelTop)}px`;
    chatPanel.style.right = 'auto';
    chatPanel.style.bottom = 'auto';
  }

  function applyFabPosition(x, y) {
    chatFab.style.setProperty('left', `${Math.round(x)}px`, 'important');
    chatFab.style.setProperty('top', `${Math.round(y)}px`, 'important');
    chatFab.style.setProperty('right', 'auto', 'important');
    chatFab.style.setProperty('bottom', 'auto', 'important');
    syncPanelToFab();
  }

  function openPanel(fullscreen) {
    chatPanel.classList.add('open');
    chatFab.classList.add('hidden');
    if (fullscreen) {
      chatPanel.classList.add('fullscreen');
      document.body.classList.add('chat-fullscreen-lock');
      chatPanel.style.left = '0';
      chatPanel.style.top = '0';
      chatPanel.style.right = '0';
      chatPanel.style.bottom = '0';
    } else {
      chatPanel.classList.remove('fullscreen');
      document.body.classList.remove('chat-fullscreen-lock');
      syncPanelToFab();
    }
    updateFullscreenButton(fullscreen);
    ensureFrameLoaded();
    requestAnimationFrame(() => postHostMode(fullscreen));
  }

  function closePanel() {
    chatPanel.classList.remove('open', 'fullscreen');
    chatFab.classList.remove('hidden');
    document.body.classList.remove('chat-fullscreen-lock');
    updateFullscreenButton(false);
    syncPanelToFab();
  }

  function togglePanel() {
    if (chatPanel.classList.contains('open')) {
      closePanel();
    } else {
      openPanel(false);
    }
  }

  function onPointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;
    isDragging = true;
    dragStarted = false;
    dragJustEnded = false;
    const rect = chatFab.getBoundingClientRect();
    startLeft = rect.left;
    startTop = rect.top;
    startX = event.clientX;
    startY = event.clientY;
    try {
      chatFab.setPointerCapture(event.pointerId);
    } catch (_) {
      // ignore
    }
  }

  function onPointerMove(event) {
    if (!isDragging) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    const distance = Math.abs(dx) + Math.abs(dy);

    if (!dragStarted && distance > 6) {
      dragStarted = true;
      chatFab.classList.add('dragging');
    }
    if (!dragStarted) return;

    const maxX = Math.max(VIEW_MARGIN, window.innerWidth - FAB_SIZE - VIEW_MARGIN);
    const maxY = Math.max(VIEW_MARGIN, window.innerHeight - FAB_SIZE - VIEW_MARGIN);
    const x = clamp(startLeft + dx, VIEW_MARGIN, maxX);
    const y = clamp(startTop + dy, VIEW_MARGIN, maxY);
    applyFabPosition(x, y);
  }

  function onPointerUp(event) {
    if (!isDragging) return;
    isDragging = false;

    if (dragStarted) {
      const rect = chatFab.getBoundingClientRect();
      saveFabPosition(rect.left, rect.top);
      dragJustEnded = true;
      setTimeout(() => {
        dragJustEnded = false;
      }, 220);
    }

    chatFab.classList.remove('dragging');
    dragStarted = false;
    try {
      chatFab.releasePointerCapture(event.pointerId);
    } catch (_) {
      // ignore
    }
  }

  chatFrame.addEventListener('load', () => {
    hideLoader();
    postHostMode(chatPanel.classList.contains('fullscreen'));
  });

  chatFab.addEventListener('click', (event) => {
    if (dragJustEnded) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    togglePanel();
  });

  chatFab.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      togglePanel();
    }
  });

  chatFab.addEventListener('dragstart', (event) => event.preventDefault());
  chatFab.addEventListener('pointerdown', onPointerDown);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
  window.addEventListener('pointercancel', onPointerUp);

  chatClose.addEventListener('click', closePanel);
  chatFullscreen.addEventListener('click', () => {
    const nextFullscreen = !chatPanel.classList.contains('fullscreen');
    openPanel(nextFullscreen);
  });

  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && chatPanel.classList.contains('open')) closePanel();
  });

  window.addEventListener('message', (event) => {
    const type = event?.data?.type;
    if (type === 'chatbot_close') {
      closePanel();
    } else if (type === 'chatbot_toggle_fullscreen') {
      const nextFullscreen = !chatPanel.classList.contains('fullscreen');
      openPanel(nextFullscreen);
    }
  });

  function initPosition() {
    const saved = loadFabPosition();
    const defaultX = window.innerWidth - FAB_SIZE - 18;
    const defaultY = window.innerHeight - FAB_SIZE - 18;
    const maxX = Math.max(VIEW_MARGIN, window.innerWidth - FAB_SIZE - VIEW_MARGIN);
    const maxY = Math.max(VIEW_MARGIN, window.innerHeight - FAB_SIZE - VIEW_MARGIN);
    const x = clamp(saved?.x ?? defaultX, VIEW_MARGIN, maxX);
    const y = clamp(saved?.y ?? defaultY, VIEW_MARGIN, maxY);
    applyFabPosition(x, y);
    saveFabPosition(x, y);
    updateFullscreenButton(false);
  }

  window.addEventListener('resize', () => {
    if (chatPanel.classList.contains('fullscreen')) return;
    const rect = chatFab.getBoundingClientRect();
    const maxX = Math.max(VIEW_MARGIN, window.innerWidth - FAB_SIZE - VIEW_MARGIN);
    const maxY = Math.max(VIEW_MARGIN, window.innerHeight - FAB_SIZE - VIEW_MARGIN);
    const x = clamp(rect.left, VIEW_MARGIN, maxX);
    const y = clamp(rect.top, VIEW_MARGIN, maxY);
    applyFabPosition(x, y);
    saveFabPosition(x, y);
  });

  initPosition();
})();
