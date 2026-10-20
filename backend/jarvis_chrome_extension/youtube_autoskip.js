(() => {
  if (window.__jarvisYouTubeAutoSkipActive) {
    return;
  }
  window.__jarvisYouTubeAutoSkipActive = true;

  const SKIP_SELECTORS = [
    '.ytp-skip-ad-button',
    '.ytp-ad-skip-button',
    '.ytp-ad-skip-button-modern',
    '.ytp-ad-skip-button-container',
    '.ytp-ad-skip-button-slot button',
    '.ytp-ad-overlay-close-button',
    '[id^="skip-button"] button',
  ];

  let wasAdShowing = false;
  let lastFastForwardSrc = '';
  let skipButtonFirstSeenAt = 0;
  let lastHardwareClickAt = 0;

  function findVisibleSkipButton() {
    for (const selector of SKIP_SELECTORS) {
      const buttons = document.querySelectorAll(selector);
      for (const btn of buttons) {
        if (!(btn instanceof HTMLElement)) continue;
        const style = window.getComputedStyle(btn);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
          continue;
        }
        const rect = btn.getBoundingClientRect();
        if (rect.width > 8 && rect.height > 8) {
          return { btn, rect };
        }
      }
    }
    return null;
  }

  function handleSkipButtons() {
    const found = findVisibleSkipButton();
    if (!found) {
      skipButtonFirstSeenAt = 0;
      return;
    }

    const { btn, rect } = found;
    const now = performance.now();
    if (!skipButtonFirstSeenAt) {
      skipButtonFirstSeenAt = now;
    }

    try {
      btn.click();
    } catch {
      // ignore
    }

    // Se o botão Pular continuar na tela após 220ms (ex: YouTube exigindo clique físico isTrusted),
    // solicita ao backend local do Jarvis um clique físico instantâneo nas coordenadas exatas do botão.
    if (
      document.visibilityState === 'visible' &&
      now - skipButtonFirstSeenAt >= 220 &&
      now - lastHardwareClickAt >= 1000 &&
      typeof chrome !== 'undefined' &&
      chrome.runtime?.sendMessage
    ) {
      lastHardwareClickAt = now;
      const dpr = window.devicePixelRatio || 1;
      const borderX = Math.max(0, (window.outerWidth - window.innerWidth * dpr) / 2);
      const chromeTop = Math.max(0, window.outerHeight - window.innerHeight * dpr - borderX);
      const screenX = Math.round(window.screenX * dpr + borderX + (rect.left + rect.width / 2) * dpr);
      const screenY = Math.round(window.screenY * dpr + chromeTop + (rect.top + rect.height / 2) * dpr);

      try {
        chrome.runtime.sendMessage({
          type: 'JARVIS_SKIP_CLICK',
          x: screenX,
          y: screenY,
        });
      } catch {
        // ignore
      }
    }
  }

  function handleYouTubeAds() {
    handleSkipButtons();

    const player = document.querySelector('#movie_player, .html5-video-player');
    const video = document.querySelector('video.html5-main-video, video');
    if (!player || !(video instanceof HTMLVideoElement)) {
      return;
    }

    const isAd =
      player.classList.contains('ad-showing') ||
      player.classList.contains('ad-interrupting') ||
      Boolean(
        document.querySelector(
          '.ytp-ad-player-overlay, .ytp-ad-player-overlay-layout, .ytp-ad-ActionInterstitial'
        )
      );

    if (isAd) {
      wasAdShowing = true;
      video.muted = true;
      try {
        video.playbackRate = 16;
      } catch {
        // ignore
      }

      if (Number.isFinite(video.duration) && video.duration > 0.3) {
        const targetTime = Math.max(0, video.duration - 0.05);
        if (video.currentTime < targetTime - 0.1 || lastFastForwardSrc !== video.currentSrc) {
          lastFastForwardSrc = video.currentSrc;
          try {
            video.currentTime = targetTime;
          } catch {
            // ignore
          }
        }
      }

      if (video.paused) {
        video.play().catch(() => {});
      }
      handleSkipButtons();
      return;
    }

    if (wasAdShowing) {
      wasAdShowing = false;
      lastFastForwardSrc = '';
      video.muted = false;
      try {
        video.playbackRate = 1;
      } catch {
        // ignore
      }
      if (video.paused) {
        video.play().catch(() => {});
      }
    }
  }

  setInterval(handleYouTubeAds, 100);

  const observer = new MutationObserver(() => {
    handleYouTubeAds();
  });

  if (document.documentElement) {
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class', 'style', 'src'],
    });
  }
})();

