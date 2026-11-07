"""Small login previews from platform-owned QR elements, never private APIs."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
import hashlib
from io import BytesIO
import re

from PIL import Image
from easel.login_qr_platforms import get_qr_platform


MAX_IMAGE_BYTES = 256 * 1024
_DATA_IMAGE = re.compile(r'^data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)$')
def official_page(page, platform='douyin') -> bool:
    config = get_qr_platform(platform)
    if config is None:
        return False
    return config.allows_url(page.url)


def valid_image(value):
    if not isinstance(value, str) or len(value) > 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 32:
        return None
    match = _DATA_IMAGE.fullmatch(value)
    if match is None:
        return None
    try:
        decoded = base64.b64decode(match[2], validate=True)
        if not decoded or len(decoded) > MAX_IMAGE_BYTES:
            return None
        with Image.open(BytesIO(decoded)) as image:
            if image.format != {'png': 'PNG', 'jpeg': 'JPEG'}[match[1]]:
                return None
            if not (64 <= image.width <= 1024 and 64 <= image.height <= 1024):
                return None
            image.verify()
    except Exception:
        return None
    return value


def _targets(page, config):
    result = [page]
    for frame in page.frames:
        if frame.parent_frame is page.main_frame and config.allows_url(frame.url, frame=True):
            result.append(frame)
            if len(result) == 5:
                break
    return result


def same_source(page, platform, observed):
    target = observed.get('_target')
    if target is None:
        return observed.get('state') not in {'ready', 'expired'}
    return (official_page(page, platform) and target in _targets(page, get_qr_platform(platform))
            and target.url == observed.get('_source_url'))


async def _frame_visible(page, target):
    if target is page or target is page.main_frame:
        return True
    host = await target.frame_element()
    try:
        return await host.evaluate(r'''element=>{
          const box=element.getBoundingClientRect(),style=getComputedStyle(element);
          if (style.display==='none' || style.visibility==='hidden' || Number(style.opacity)===0 ||
              box.width<=0 || box.height<=0 || box.left<0 || box.top<0 || box.right>innerWidth || box.bottom>innerHeight) return false;
          const intersects=node=>{const rect=node.getBoundingClientRect(),style=getComputedStyle(node);
            return style.display!=='none' && style.visibility!=='hidden' && Number(style.opacity)!==0 &&
              rect.width>0 && rect.height>0 && rect.left<box.right && rect.right>box.left && rect.top<box.bottom && rect.bottom>box.top;};
          if (Array.from(document.querySelectorAll('input,textarea,select,[contenteditable="true"]')).some(intersects)) return false;
          return [0.1,0.5,0.9].every(x=>[0.1,0.5,0.9].every(y=>
            document.elementFromPoint(box.left+box.width*x,box.top+box.height*y)===element));
        }''')
    finally:
        with suppress(Exception):
            await asyncio.wait_for(host.dispose(), timeout=.5)


async def _clip_qr(page, handle):
    """Capture only the bounded visible QR element, without font-ready waits."""
    guard = r'''element => {
      if (!['IMG','CANVAS'].includes(element.tagName)) return false;
      const box=element.getBoundingClientRect();
      const overlaps=other=>other.width>0 && other.height>0 && other.left<box.right && other.right>box.left &&
        other.top<box.bottom && other.bottom>box.top;
      if (Array.from(document.querySelectorAll('input,textarea,select,[contenteditable="true"]')).some(node=>{
        const style=getComputedStyle(node);return style.display!=='none' && style.visibility!=='hidden' &&
          Number(style.opacity)!==0 && overlaps(node.getBoundingClientRect());
      })) return false;
      return [0.1,0.5,0.9].every(x=>[0.1,0.5,0.9].every(y=>
        document.elementFromPoint(box.left+box.width*x,box.top+box.height*y)===element));
    }'''
    owner = await handle.owner_frame()
    if owner is None or not await _frame_visible(page, owner) or not await handle.evaluate(guard):
        return None
    box = await handle.bounding_box()
    if not box or not (64 <= box['width'] <= 1024 and 64 <= box['height'] <= 1024):
        return None
    cdp = await page.context.new_cdp_session(page)
    try:
        metrics = await cdp.send('Page.getLayoutMetrics')
        viewport = metrics['cssLayoutViewport']
        if (box['x'] < 0 or box['y'] < 0 or box['x'] + box['width'] > viewport['clientWidth'] + 1
                or box['y'] + box['height'] > viewport['clientHeight'] + 1):
            return None
        result = await cdp.send('Page.captureScreenshot', {'format': 'png', 'fromSurface': True,
            'captureBeyondViewport': False, 'clip': {'x': box['x'] + viewport['pageX'],
                'y': box['y'] + viewport['pageY'], 'width': box['width'], 'height': box['height'], 'scale': 1}})
        return valid_image('data:image/png;base64,' + result['data']) if (
            await _frame_visible(page, owner) and await handle.evaluate(guard)) else None
    finally:
        with suppress(Exception):
            await asyncio.wait_for(cdp.detach(), timeout=.5)


async def _read_target(page, target, config):
    # No input values, storage or application state are read. The selector list
    # is a static platform adapter, not an HTTP caller or page-provided selector.
    if not await _frame_visible(page, target):
        return {'state': 'starting'}
    observed = await target.evaluate(r'''config => {
      const visible = element => {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        for (let node=element; node instanceof Element; node=node.parentElement) {
          const style=getComputedStyle(node);
          if (style.display==='none' || style.visibility==='hidden' || Number(style.opacity)===0) return false;
        }
        return true;
      };
      // innerText on a visible ancestor still includes opacity:0 descendants.
      // Build short labels from visible text nodes instead, while preserving
      // labels split across spans and never reading editable field contents.
      const textCache = new WeakMap();
      const visibleText = element => {
        if (textCache.has(element)) return textCache.get(element);
        if (!visible(element) || ['INPUT','TEXTAREA','SELECT','OPTION'].includes(element.tagName) || element.isContentEditable) return '';
        let text='';
        for (const child of element.childNodes) {
          if (child.nodeType===Node.TEXT_NODE) text+=child.textContent || '';
          else if (child instanceof Element) text+=visibleText(child);
          if (text.length>100) { text=text.slice(0,101); break; }
        }
        textCache.set(element,text);
        return text;
      };
      const labelElements = Array.from(document.querySelectorAll('button,span,div,p,h1,h2,h3,label'))
        .filter(visible).map(element => ({element,text:visibleText(element).trim()})).filter(item => item.text && item.text.length <= 100);
      const labels = labelElements.map(item=>item.text);
      const matches = patterns => patterns.some(pattern => labels.some(text => new RegExp(pattern,'i').test(text)));
      const expired = matches(config.expired_text) || config.expired_selectors.some(selector =>
        Array.from(document.querySelectorAll(selector)).some(visible));
      const confirmation = matches(config.scanned_text);
      const verification = matches(config.verification_text);
      const fields = Array.from(document.querySelectorAll('input')).filter(visible);
      const verificationField = fields.some(element => /验证码/.test(element.getAttribute('placeholder') || '') ||
        element.getAttribute('autocomplete') === 'one-time-code');
      const verificationLabels = labelElements.filter(item => config.verification_text.some(pattern =>
        new RegExp(pattern,'i').test(item.text)));
      const blockedByVerification = qr => {
        if (!verificationLabels.length) return false;
        const rect=qr.getBoundingClientRect();
        return [0.1,0.5,0.9].some(x=>[0.1,0.5,0.9].some(y=>{
          const top=document.elementFromPoint(rect.left+rect.width*x,rect.top+rect.height*y);
          if (top===qr) return false;
          for (let panel=top; panel && panel!==document.body; panel=panel.parentElement) {
            // The normal login card contains both QR and SMS alternatives;
            // only a separate overlaid verification panel blocks scanning.
            if (panel.contains(qr)) continue;
            const style=getComputedStyle(panel);
            if ((panel.getAttribute('role')==='dialog' || panel.getAttribute('aria-modal')==='true' ||
                 ['fixed','absolute'].includes(style.position)) && verificationLabels.some(item=>panel.contains(item.element))) return true;
          }
          return false;
        }));
      };
      let candidate = null, blockedVerification = false;
      for (const selector of config.selectors) {
        const elements=Array.from(document.querySelectorAll(selector));
        const index=elements.findIndex(element=>{
          const rect=element.getBoundingClientRect();
          return ['IMG','CANVAS'].includes(element.tagName) && visible(element) && rect.left>=0 && rect.top>=0 &&
            rect.right<=innerWidth && rect.bottom<=innerHeight;
        });
        if (index < 0) continue;
        const element=elements[index];
        if (blockedByVerification(element)) {
          blockedVerification=true; candidate={selector,index,tag:element.tagName}; break;
        }
        let value=element.getAttribute('src');
        const resource=element.tagName==='IMG' ? (element.currentSrc || element.src) : '';
        if (resource.startsWith('data:image/')) value=resource;
        if (element.tagName==='CANVAS') {
          if (element.width<64 || element.height<64 || element.width>1024 || element.height>1024) continue;
          try { value=element.toDataURL('image/png'); } catch {}
        }
        if (value && value.length > 350000) value=null;
        candidate={selector,index,image:value,resource:resource.length<=16384 ? resource : '',tag:element.tagName,loaded:
          element.tagName!=='IMG' || (element.complete && element.naturalWidth>0)};
        break;
      }
      return {expired,confirmation,verification:verification||verificationField,blockedVerification,candidate,
        documentUrl:location.href,documentEpoch:performance.timeOrigin};
    }''', config.asdict())
    candidate = observed.get('candidate')
    image = valid_image(candidate.get('image')) if candidate else None
    source = hashlib.sha256(repr([id(target), observed['documentUrl'], observed['documentEpoch'], candidate]).encode()).hexdigest()
    private = {'_source_key': source, '_target': target, '_source_url': observed['documentUrl']}
    if observed.get('blockedVerification'):
        return {'state': 'verification_required', **private}
    if observed.get('expired'):
        return {'state': 'expired', **private, **({'image': image} if image else {})}
    if observed.get('confirmation'):
        return {'state': 'starting', **private, 'message': '已扫码，请在手机上确认，随后核验账号身份'}
    if candidate and not image:
        # Never send arbitrary URLs to the caller or fetch them server-side.
        # A loaded HTTPS/blob image or a canvas may be captured in its exact
        # visible element; malformed data images are rejected rather than masked.
        if candidate.get('loaded') and ((candidate.get('resource') or '').startswith(('https://', 'blob:'))
                                        or candidate['tag'] == 'CANVAS'):
            handle = await target.locator(candidate['selector']).nth(candidate['index']).element_handle()
            if handle is not None:
                try:
                    image = await _clip_qr(page, handle)
                finally:
                    with suppress(Exception):
                        await asyncio.wait_for(handle.dispose(), timeout=.5)
    if image:
        return {'state': 'ready', 'image': image, **private}
    if observed.get('verification'):
        return {'state': 'verification_required', **private}
    return {'state': 'unavailable' if candidate else 'starting', **private}


async def read_qr(page, platform='douyin'):
    if page.url == 'about:blank':
        return {'state': 'starting'}
    if not official_page(page, platform):
        return {'state': 'unavailable'}
    config = get_qr_platform(platform)
    fallback = {'state': 'starting'}
    for target in _targets(page, config):
        observed = await _read_target(page, target, config)
        if observed['state'] in {'ready', 'expired'}:
            if official_page(page, platform) and target in _targets(page, config):
                return observed
            return {'state': 'starting'}
        if observed['state'] != 'starting' or 'message' in observed:
            fallback = observed
    if not official_page(page, platform):
        return {'state': 'unavailable'}
    return fallback


async def refresh_control(page, platform='douyin', observed=None):
    """Return the current visible official refresh element, never a guessed point."""
    if not official_page(page, platform):
        return None
    config = get_qr_platform(platform)
    targets = _targets(page, config)
    target = (observed or {}).get('_target', page)
    if target not in targets:
        return None
    async def visible(candidate):
        # Playwright considers opacity:0 elements visible/actionable. A stale
        # hidden refresh control must not win over the current platform control.
        return await candidate.evaluate(r'''element=>{
          const rect=element.getBoundingClientRect();
          if (rect.width<=0 || rect.height<=0) return false;
          for (let node=element; node instanceof Element; node=node.parentElement) {
            const style=getComputedStyle(node);
            if (style.display==='none' || style.visibility==='hidden' || Number(style.opacity)===0) return false;
          }
          return true;
        }''')
    for pattern in config.refresh_text:
        candidates = target.get_by_text(re.compile(pattern, re.I))
        for index in range(min(await candidates.count(), 8)):
            candidate = candidates.nth(index)
            if await visible(candidate):
                return await candidate.element_handle()
    for selector in config.refresh_selectors:
        candidates = target.locator(selector)
        for index in range(min(await candidates.count(), 8)):
            candidate = candidates.nth(index)
            if await visible(candidate):
                return await candidate.element_handle()
    return None
