"""
browser/engine.py — Browser factory.

Yields a playwright Page with anti-detect settings for either:
  • 'camoufox'  — patched Firefox (best fingerprint resistance)
  • 'playwright' — Chromium with stealth init-script

Headed / Headless mode:
  headless=True  (default) — invisible, suitable for batch automation
  headless=False            — visible browser window, useful for debugging

Usage:
    # headless (default)
    async with create_page(engine="playwright", proxy="http://...") as page:
        ...

    # headed (visible window)
    async with create_page(engine="playwright", headless=False, slow_mo=80) as page:
        ...

CLI smoke-test:
    python -m src.browser.engine [camoufox|playwright] [--headed]
"""
from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

# ── Fingerprint pools ─────────────────────────────────────────────────────

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
    {"width": 1536, "height": 864},
]

_CHROMIUM_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

_TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Los_Angeles",
    "America/Denver", "Europe/London", "Europe/Berlin",
]

# ── Mobile fingerprint pools ──────────────────────────────────────────────

_MOBILE_UAS = [
    # iOS Safari
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    # Android Chrome
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
]

_MOBILE_VIEWPORTS = [
    # (width, height, device_scale_factor) — portrait
    {"width": 390, "height": 844, "device_scale_factor": 3.0},    # iPhone 14/15
    {"width": 430, "height": 932, "device_scale_factor": 3.0},    # iPhone 15 Plus
    {"width": 412, "height": 915, "device_scale_factor": 2.625},  # Pixel 8
    {"width": 360, "height": 780, "device_scale_factor": 3.0},    # Samsung S23
]

# Mobile stealth init-script (replaces _STEALTH_JS for mobile contexts).
# Sets mobile-appropriate navigator/screen properties from scratch so there
# are no conflicts with the desktop stealth script.
_MOBILE_STEALTH_JS = """
(function() {
const _ua = navigator.userAgent || '';
const _isIOS = /iPhone|iPad|iPod/.test(_ua);

// 1. Remove webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 2. Mobile platform / language / hardware
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'platform',  {get: () => _isIOS ? 'iPhone' : 'Linux armv8l'});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory',        {get: () => 4});
Object.defineProperty(navigator, 'maxTouchPoints',      {get: () => 5});

// 3. No plugins on mobile
Object.defineProperty(navigator, 'plugins',   {get: () => []});
Object.defineProperty(navigator, 'mimeTypes', {get: () => []});

// 4. Minimal chrome object for Android; omit for iOS Safari
if (!_isIOS) {
    window.chrome = {
        app: {isInstalled: false},
        runtime: {id: undefined, connect: function(){}, sendMessage: function(){}},
        csi: function(){},
        loadTimes: function(){}
    };
}

// 5. Permissions
if (navigator.permissions && navigator.permissions.query) {
    const _origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({state: Notification.permission, onchange: null})
            : _origQuery(params);
}

// 6. outerWidth/Height (mobile has no chrome chrome)
if (!window.outerWidth || window.outerWidth === 0) {
    Object.defineProperty(window, 'outerWidth',  {get: () => window.innerWidth});
}
if (!window.outerHeight || window.outerHeight === 0) {
    Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight});
}

// 7. Remove Playwright artefacts
try { delete window.__playwright; } catch(e){}
try { delete window.__pw_manual; } catch(e){}
try { delete window.__playwright_evaluate_expression; } catch(e){}

// 8. Mobile network connection
if (!navigator.connection) {
    Object.defineProperty(navigator, 'connection', {
        get: () => ({rtt: 80, effectiveType: '4g', downlink: 8.5, saveData: false})
    });
}

// 9. Mobile WebGL (Adreno / Apple GPU)
(function() {
    const _v = _isIOS ? 'Apple Inc.' : 'Qualcomm';
    const _r = _isIOS ? 'Apple GPU'  : 'Adreno (TM) 740';
    function _patchCtx(Ctx) {
        if (!Ctx) return;
        const _orig = Ctx.prototype.getParameter;
        Ctx.prototype.getParameter = function(p) {
            if (p === 37445) return _v;
            if (p === 37446) return _r;
            return _orig.call(this, p);
        };
    }
    _patchCtx(window.WebGLRenderingContext);
    _patchCtx(window.WebGL2RenderingContext);
})();

// 10. Canvas noise
(function() {
    const _origGID = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {
        const d = _origGID.call(this, sx, sy, sw, sh);
        for (let i = 0; i < d.data.length; i += 4) {
            d.data[i]   ^= (Math.random() < 0.3 ? 1 : 0);
            d.data[i+1] ^= (Math.random() < 0.3 ? 1 : 0);
            d.data[i+2] ^= (Math.random() < 0.3 ? 1 : 0);
        }
        return d;
    };
})();

// 11. Portrait orientation
try { Object.defineProperty(window, 'orientation', {get: () => 0}); } catch(e){}

// 12. Screen matches mobile viewport
(function() {
    const W = window.innerWidth  || 390;
    const H = window.innerHeight || 844;
    try { Object.defineProperty(screen, 'width',       {get: () => W}); } catch(e){}
    try { Object.defineProperty(screen, 'height',      {get: () => H}); } catch(e){}
    try { Object.defineProperty(screen, 'availWidth',  {get: () => W}); } catch(e){}
    try { Object.defineProperty(screen, 'availHeight', {get: () => H}); } catch(e){}
})();

// 13. Conceal automation stack frames
(function() {
    const _origPrepare = Error.prepareStackTrace;
    Error.prepareStackTrace = function(err, stack) {
        const filtered = stack.filter(f => {
            const src = f.getFileName() || '';
            return !src.includes('playwright') && !src.includes('__pw_');
        });
        return _origPrepare ? _origPrepare(err, filtered) : filtered.map(f => '    at ' + f).join('\\n');
    };
})();

})(); // end IIFE
"""

# Stealth init-script injected into every Chromium page.
# Covers the most common headless-Chromium fingerprint leaks that
# Auth0 / Cloudflare bot-detection scripts probe.
_STEALTH_JS = """
(function() {
// 1. Remove navigator.webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 2. Languages / platform
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'platform',  {get: () => 'Win32'});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory',        {get: () => 8});
Object.defineProperty(navigator, 'maxTouchPoints',      {get: () => 0});

// 3. Realistic plugins list (Chromium headless has 0 plugins by default — big tell)
const _fakePlugins = ['Chrome PDF Plugin','Chrome PDF Viewer','Native Client'].map((name, i) => {
    const p = {name, description: name, filename: ['internal-pdf-viewer','mhjfbmdgcfjbbpaeojofohoefgiehjai','internal-nacl-plugin'][i], length:0};
    return p;
});
Object.defineProperty(navigator, 'plugins', {get: () => _fakePlugins});
Object.defineProperty(navigator, 'mimeTypes', {get: () => []});

// 4. Full chrome object (headless Chromium exposes only a stub)
window.chrome = {
    app: {
        isInstalled: false,
        InstallState: {DISABLED:'disabled',INSTALLED:'installed',NOT_INSTALLED:'not_installed'},
        RunningState: {CANNOT_RUN:'cannot_run',READY_TO_RUN:'ready_to_run',RUNNING:'running'},
        getDetails: function(){},
        getIsInstalled: function(){},
        runningState: function(){}
    },
    runtime: {
        OnInstalledReason: {INSTALL:'install',UPDATE:'update',CHROME_UPDATE:'chrome_update',SHARED_MODULE_UPDATE:'shared_module_update'},
        PlatformArch: {ARM:'arm',ARM64:'arm64',X86_32:'x86-32',X86_64:'x86-64'},
        PlatformOs: {ANDROID:'android',CROS:'cros',LINUX:'linux',MAC:'mac',WIN:'win'},
        id: undefined,
        connect: function(){},
        sendMessage: function(){}
    },
    csi: function(){},
    loadTimes: function(){}
};

// 5. Permissions — fix the 'notifications' probe (headless returns 'denied' synchronously)
if (navigator.permissions && navigator.permissions.query) {
    const _origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({state: Notification.permission, onchange: null})
            : _origQuery(params);
}

// 6. outerWidth / outerHeight are 0 in headless — real browsers include chrome chrome (~88px)
if (!window.outerWidth || window.outerWidth === 0) {
    Object.defineProperty(window, 'outerWidth',  {get: () => window.innerWidth});
}
if (!window.outerHeight || window.outerHeight === 0) {
    Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight + 88});
}

// 7. Remove Playwright / CDP artefacts left in the global scope
try { delete window.__playwright; } catch(e){}
try { delete window.__pw_manual; } catch(e){}
try { delete window.__playwright_evaluate_expression; } catch(e){}

// 8. ConnectionRTT — headless has no connection object
if (!navigator.connection) {
    Object.defineProperty(navigator, 'connection', {
        get: () => ({rtt:50, effectiveType:'4g', downlink:10, saveData:false})
    });
}

// 9. WebGL fingerprint — spoof vendor/renderer (Cloudflare probes these)
(function() {
    const _vendors = [
        'Google Inc. (Intel)',
        'Google Inc. (NVIDIA)',
    ];
    const _renderers = [
        'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)',
        'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    ];
    const _v = _vendors[Math.floor(Math.random() * _vendors.length)];
    const _r = _renderers[Math.floor(Math.random() * _renderers.length)];
    function _patchCtx(Ctx) {
        if (!Ctx) return;
        const _orig = Ctx.prototype.getParameter;
        Ctx.prototype.getParameter = function(p) {
            if (p === 37445) return _v;   // UNMASKED_VENDOR_WEBGL
            if (p === 37446) return _r;   // UNMASKED_RENDERER_WEBGL
            return _orig.call(this, p);
        };
    }
    _patchCtx(window.WebGLRenderingContext);
    _patchCtx(window.WebGL2RenderingContext);
})();

// 10. Canvas 2D fingerprint noise — add ±1 LSB noise to getImageData
(function() {
    const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {
        const d = _origGetImageData.call(this, sx, sy, sw, sh);
        for (let i = 0; i < d.data.length; i += 4) {
            d.data[i]   ^= (Math.random() < 0.3 ? 1 : 0);
            d.data[i+1] ^= (Math.random() < 0.3 ? 1 : 0);
            d.data[i+2] ^= (Math.random() < 0.3 ? 1 : 0);
        }
        return d;
    };
    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, q) {
        // Nudge a single pixel so hash changes each invocation
        try {
            const ctx = this.getContext('2d');
            if (ctx) {
                const px = _origGetImageData.call(ctx, 0, 0, 1, 1);
                px.data[0] ^= 1;
                ctx.putImageData(px, 0, 0);
            }
        } catch(e) {}
        return _origToDataURL.call(this, type, q);
    };
})();

// 11. Audio fingerprint — add imperceptible noise to AnalyserNode output
(function() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    const _origCA = AC.prototype.createAnalyser;
    AC.prototype.createAnalyser = function() {
        const an = _origCA.call(this);
        const _origGFF = an.getFloatFrequencyData.bind(an);
        an.getFloatFrequencyData = function(arr) {
            _origGFF(arr);
            for (let i = 0; i < arr.length; i++) {
                arr[i] += (Math.random() - 0.5) * 1e-4;
            }
        };
        return an;
    };
})();

// 12. screen — ensure screen size matches viewport (headless mismatch is detectable)
(function() {
    const W = window.innerWidth  || 1920;
    const H = window.innerHeight || 1080;
    if (screen.width < W) {
        try { Object.defineProperty(screen, 'width',  {get: () => W}); } catch(e) {}
        try { Object.defineProperty(screen, 'availWidth',  {get: () => W}); } catch(e) {}
    }
    if (screen.height < H) {
        try { Object.defineProperty(screen, 'height', {get: () => H}); } catch(e) {}
        try { Object.defineProperty(screen, 'availHeight', {get: () => H - 40}); } catch(e) {}
    }
})();

// 13. Conceal automation-related Error stack frames
(function() {
    const _origPrepare = Error.prepareStackTrace;
    Error.prepareStackTrace = function(err, stack) {
        const filtered = stack.filter(f => {
            const src = f.getFileName() || '';
            return !src.includes('playwright') && !src.includes('__pw_');
        });
        return _origPrepare ? _origPrepare(err, filtered) : filtered.map(f => '    at ' + f).join('\\n');
    };
})();

})(); // end IIFE
"""


# ── Proxy parsing ─────────────────────────────────────────────────────────

def _parse_proxy(proxy_url: str) -> dict:
    """Convert 'http://user:pass@host:port' → playwright proxy dict."""
    p = urlparse(proxy_url)
    scheme = p.scheme or "http"
    host   = p.hostname or ""
    port   = p.port or 8080
    result: dict = {"server": f"{scheme}://{host}:{port}"}
    if p.username:
        result["username"] = p.username
    if p.password:
        result["password"] = p.password
    return result


# ── Context managers ──────────────────────────────────────────────────────

@asynccontextmanager
async def create_page(
    engine: str = "playwright",
    proxy: Optional[str] = None,
    headless: bool = True,
    slow_mo: int = 0,
    mobile: bool = False,
):
    """
    Async context manager that yields a ready-to-use playwright Page.
    Cleans up the browser on exit.

    Parameters
    ----------
    engine   : 'playwright' (Chromium) | 'camoufox' (Firefox)
    proxy    : optional proxy URL, e.g. 'http://user:pass@host:port'
    headless : True  → invisible batch mode (default)
               False → visible headed window (debug / manual-assist mode)
    slow_mo  : extra delay in ms between actions (useful in headed mode, e.g. 80)
    mobile   : True → emulate a mobile device fingerprint.
               Playwright: uses a random iOS/Android UA, portrait viewport,
                 is_mobile=True, has_touch=True, and _MOBILE_STEALTH_JS.
               Camoufox: uses a mobile-sized window + screen constraints
                 (camoufox is Firefox-based and has no Android OS support,
                 so the UA stays Firefox/Linux but the viewport is mobile-sized).
    """
    proxy_cfg = _parse_proxy(proxy) if proxy else None
    viewport  = random.choice(_VIEWPORTS)
    timezone  = random.choice(_TIMEZONES)

    mode_label = "headless" if headless else "HEADED"
    mobile_label = " [MOBILE]" if mobile else ""
    logger.info(
        f"[engine] {engine} launching "
        f"[{mode_label}{mobile_label}, proxy={bool(proxy_cfg)}, slow_mo={slow_mo}ms]"
    )

    if engine == "camoufox":
        async with _camoufox_page(proxy_cfg, viewport, headless, slow_mo, mobile=mobile) as page:
            yield page
    else:
        async with _playwright_page(proxy_cfg, viewport, timezone, headless, slow_mo, mobile=mobile) as page:
            yield page


@asynccontextmanager
async def create_oauth_mobile_page(
    source_page,
    proxy: Optional[str] = None,
    headless: bool = True,
    slow_mo: int = 0,
):
    """
    Async context manager — yields a fresh Chromium page with a **mobile**
    browser fingerprint, intended for the OAuth PKCE login flow.

    Cookies from ``source_page``'s browser context are copied into the new
    mobile context so any existing auth.openai.com session is preserved and
    Auth0 can skip re-authentication when cookies are still valid.

    Mobile characteristics:
      • Random iOS Safari or Android Chrome User-Agent
      • Portrait viewport matching a real device (390×844 … 412×915)
      • ``is_mobile=True``, ``has_touch=True``, ``device_scale_factor`` set
      • ``_MOBILE_STEALTH_JS`` patches navigator.platform, maxTouchPoints,
        WebGL GPU strings, canvas noise, screen size, etc.
    """
    import sys
    from playwright.async_api import async_playwright

    proxy_cfg = _parse_proxy(proxy) if proxy else None
    mv  = random.choice(_MOBILE_VIEWPORTS)
    ua  = random.choice(_MOBILE_UAS)
    tz  = random.choice(_TIMEZONES)

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-notifications",
        "--disable-popup-blocking",
    ]
    if sys.platform.startswith("linux"):
        launch_args += ["--no-sandbox", "--disable-dev-shm-usage"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
            proxy=proxy_cfg,
            args=launch_args,
        )
        context = await browser.new_context(
            user_agent=ua,
            viewport={"width": mv["width"], "height": mv["height"]},
            device_scale_factor=mv["device_scale_factor"],
            is_mobile=True,
            has_touch=True,
            locale="en-US",
            timezone_id=tz,
            java_script_enabled=True,
        )
        # Transfer cookies from the registration session so Auth0 sees the
        # same logged-in state in the mobile context.
        try:
            cookies = await source_page.context.cookies()
            if cookies:
                await context.add_cookies(cookies)
                logger.info(
                    f"[engine] Mobile OAuth context — transferred {len(cookies)} cookies"
                )
        except Exception as exc:
            logger.warning(f"[engine] Cookie transfer to mobile context failed: {exc}")

        await context.add_init_script(_MOBILE_STEALTH_JS)
        page = await context.new_page()
        logger.info(
            f"[engine] Mobile OAuth context launched — "
            f"ua={ua[:55]}…  vp={mv['width']}×{mv['height']}"
        )
        try:
            yield page
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


@asynccontextmanager
async def _camoufox_page(proxy_cfg, viewport, headless: bool, slow_mo: int, mobile: bool = False):
    """Launch a camoufox (Firefox) page with realistic fingerprinting.

    Supported launch_options: os, locale, block_webrtc, humanize, window, geoip, proxy, headless
    Note: 'timezone' and 'viewport' are NOT accepted — use 'window' and 'geoip' instead.

    Mobile mode: sets a portrait mobile window size and constrains the BrowserForge
    screen fingerprint to mobile dimensions.  Camoufox is Firefox-based so there is
    no Android OS support; the UA stays Firefox/Linux but the viewport matches a real
    mobile device screen.
    """
    try:
        from camoufox.async_api import AsyncCamoufox  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "camoufox is not installed or its browser is not fetched.\n"
            "Run:  uv run python -m camoufox fetch"
        ) from exc

    # geoip=True: camoufox resolves real timezone/locale from proxy's exit IP
    # humanize=True: built-in human-like timing for mouse events (helps vs bot-detect)
    # block_webrtc=True: prevent WebRTC from leaking local IP through proxy
    # window expects (width, height) tuple, NOT a dict
    #
    # BrowserForge coverage issue: some OS/window-size combinations have too few
    # fingerprints in the dataset and raise "No headers based on this input can be
    # generated."  This often manifests as a first-run failure because the randomly
    # chosen viewport happens to fall in a sparse part of the DB.
    # Strategy: outer retry loop (up to _MAX_INIT_ATTEMPTS) that re-randomises the
    # viewport and OS on each attempt; inner OS-fallback loop tries all three OSes
    # with the same viewport before moving on.
    _BF_REJECT_KEYWORDS = (
        "no headers", "requirements you specified", "relax",
        "could not generate", "generate http", "browserforge",
        "no fingerprint", "no browser fingerprint",
    )
    _MAX_INIT_ATTEMPTS = 3

    cam_cm = None
    browser = None

    for _init_attempt in range(_MAX_INIT_ATTEMPTS):
        extra_kwargs: dict = {}

        if mobile:
            mv = random.choice(_MOBILE_VIEWPORTS)
            win_size = (mv["width"], mv["height"])
            os_choice = "linux"
            try:
                from browserforge.fingerprints import Screen as _BFScreen  # type: ignore
                extra_kwargs["screen"] = _BFScreen(
                    max_width=mv["width"] + 30,
                    max_height=mv["height"] + 30,
                )
            except Exception:
                pass  # BrowserForge Screen not available — window size alone is enough
        else:
            # Randomize OS — Windows is most common, include macOS for variety.
            # On retries pick a fresh random viewport to escape DB coverage gaps.
            os_choice = random.choice(["windows", "windows", "windows", "macos"])
            cur_viewport = viewport if _init_attempt == 0 else random.choice(_VIEWPORTS)
            win_size = (cur_viewport["width"], cur_viewport["height"])

        _os_fallback = [os_choice] + [o for o in ("linux", "macos", "windows") if o != os_choice]

        for _os_try in _os_fallback:
            try:
                cam_cm = AsyncCamoufox(
                    headless=headless,
                    proxy=proxy_cfg,
                    os=_os_try,
                    locale="en-US",
                    block_webrtc=True,
                    humanize=True,
                    window=win_size,          # (width, height) tuple
                    geoip=proxy_cfg is not None,
                    **extra_kwargs,
                )
                browser = await cam_cm.__aenter__()
                if _os_try != os_choice:
                    logger.info(
                        f"[engine] Camoufox: fell back to OS={_os_try!r} "
                        f"(BrowserForge rejected {os_choice!r})"
                    )
                break
            except Exception as _bf_exc:
                _msg = str(_bf_exc).lower()
                if any(kw in _msg for kw in _BF_REJECT_KEYWORDS):
                    logger.warning(
                        f"[engine] Camoufox OS={_os_try!r} BrowserForge rejection — "
                        f"trying next OS… ({_bf_exc})"
                    )
                    cam_cm = None
                    continue
                raise

        if browser is not None:
            break  # successfully launched

        # All OS options exhausted for this attempt — retry with fresh random values
        if _init_attempt < _MAX_INIT_ATTEMPTS - 1:
            logger.warning(
                f"[engine] Camoufox: BrowserForge rejected all OS options "
                f"(attempt {_init_attempt + 1}/{_MAX_INIT_ATTEMPTS}, "
                f"viewport={win_size}) — retrying with fresh fingerprint…"
            )
            await asyncio.sleep(0.5)

    if browser is None or cam_cm is None:
        raise RuntimeError(
            "Camoufox: BrowserForge could not generate HTTP headers after "
            f"{_MAX_INIT_ATTEMPTS} attempts. "
            "Try updating camoufox/browserforge: `uv run python -m camoufox fetch`"
        )

    page = None
    try:
        page = await browser.new_page()
        if slow_mo > 0:
            # camoufox doesn't expose slow_mo natively; approximate via monkey-patch
            _orig_click = page.click

            async def _slow_click(*a, **kw):
                await asyncio.sleep(slow_mo / 1000)
                return await _orig_click(*a, **kw)

            page.click = _slow_click  # type: ignore[method-assign]
        yield page
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        try:
            await cam_cm.__aexit__(None, None, None)
        except Exception:
            pass


@asynccontextmanager
async def _playwright_page(proxy_cfg, viewport, timezone, headless: bool, slow_mo: int, mobile: bool = False):
    """Launch a Chromium page via playwright with stealth patches.

    Mobile mode: uses a random iOS/Android User-Agent, a portrait mobile viewport
    (is_mobile=True, has_touch=True, device_scale_factor), and injects
    _MOBILE_STEALTH_JS instead of the desktop _STEALTH_JS.
    """
    import sys
    from playwright.async_api import async_playwright  # type: ignore

    if mobile:
        mv  = random.choice(_MOBILE_VIEWPORTS)
        ua  = random.choice(_MOBILE_UAS)
        vp  = {"width": mv["width"], "height": mv["height"]}
        stealth_js = _MOBILE_STEALTH_JS
        ctx_extra: dict = {
            "device_scale_factor": mv["device_scale_factor"],
            "is_mobile":           True,
            "has_touch":           True,
        }
    else:
        ua  = random.choice(_CHROMIUM_UAS)
        vp  = viewport
        stealth_js = _STEALTH_JS
        ctx_extra = {}

    # Core anti-detect flag — always needed.
    # --no-sandbox is a strong automation signal AND unnecessary on Windows;
    # --disable-dev-shm-usage is Linux-only.  Only add them on Linux.
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-notifications",
        "--disable-popup-blocking",
        "--disable-save-password-bubble",
    ]
    if sys.platform.startswith("linux"):
        launch_args += ["--no-sandbox", "--disable-dev-shm-usage"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
            proxy=proxy_cfg,
            args=launch_args,
        )
        context = await browser.new_context(
            user_agent=ua,
            viewport=vp,
            locale="en-US",
            timezone_id=timezone,
            java_script_enabled=True,
            **ctx_extra,
        )
        await context.add_init_script(stealth_js)
        page = await context.new_page()
        try:
            yield page
        finally:
            await context.close()
            await browser.close()


# ── CLI smoke-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    async def _main() -> None:
        args   = sys.argv[1:]
        engine  = next((a for a in args if not a.startswith("--")), "playwright")
        headed  = "--headed" in args
        slow_mo = 80 if headed else 0

        screenshot = "test_screenshot.png"
        async with create_page(engine=engine, headless=not headed, slow_mo=slow_mo) as page:
            await page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=30_000)
            await page.screenshot(path=screenshot)
            title = await page.title()

        mode = "headed" if headed else "headless"
        print(f"Browser launched: {engine} ({mode})")
        print(f"Page title: {title}")
        print(f"Screenshot saved to {screenshot}")

    asyncio.run(_main())

