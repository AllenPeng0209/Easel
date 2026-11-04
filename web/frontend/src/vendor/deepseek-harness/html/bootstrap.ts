/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-sidebar-documentpreview/src/client/html/bootstrap.ts
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: static image binary payload extension and local byte-reader type.
 * See integrations/deepseek-harness/LICENSE.
 */
/** A fixed bootstrap runs inside the opaque iframe; no Host callbacks enter its document. */
import { decodeText, encodeText, encodeBytes } from './bytes.ts'

/** One statically declared local script or stylesheet, already read under the source file's authority. */
export interface HtmlAsset {
  readonly kind: 'script' | 'stylesheet' | 'image'
  readonly mime?: string
  /** Original HTML attribute, not a Host absolute path. */
  readonly reference: string
  readonly data: Uint8Array<ArrayBuffer>
}

/** Complete bytes for one document; dependencies are finite and never requested by iframe messages. */
export interface HtmlBundle {
  readonly data: Uint8Array<ArrayBuffer>
  readonly assets: readonly HtmlAsset[]
  /** Easel-owned layout reporting is inserted only inside the opaque frame. */
  readonly preview?: { readonly baseUrl: string; readonly script: string }
}

/**
 * Build the outer iframe document. Its resource URLs are created inside the sandbox,
 * because that opaque origin cannot load resource URLs created by the parent.
 * @param bundle - complete HTML bytes and optional static dependencies.
 * @returns bootstrap HTML; invalid UTF-8 throws before navigation.
 */
export function createHtmlDocument(bundle: HtmlBundle): string {
  const payload = encodeText(JSON.stringify({
    html: decodeText(bundle.data),
    assets: bundle.assets.map(asset => asset.kind === 'image'
      ? { kind: asset.kind, reference: asset.reference, base64: encodeBytes(asset.data), mime: asset.mime }
      : { kind: asset.kind, reference: asset.reference, text: decodeText(asset.data) }),
    preview: bundle.preview,
  }))
  return `<!doctype html><meta charset="utf-8"><script>(()=>{
const bytes=data=>Uint8Array.from(atob(data),character=>character.charCodeAt(0));
const text=data=>new TextDecoder('utf-8',{fatal:true}).decode(bytes(data));
const bundle=JSON.parse(text("${payload}"));
let html=bundle.html;
if(bundle.assets.length||bundle.preview){
  const parsed=new DOMParser().parseFromString(html,'text/html');
  for(const asset of bundle.assets){
    const script=asset.kind==='script';
    const image=asset.kind==='image';
    const url=URL.createObjectURL(new Blob([image?bytes(asset.base64):asset.text],{type:script?'application/javascript':image?asset.mime:'text/css'}));
    const attribute=script||image?'src':'href';
    for(const element of parsed.querySelectorAll(script?'script[src]':image?'img[src]':'link[rel~="stylesheet" i][href]')){
      if(element.getAttribute(attribute)===asset.reference)element.setAttribute(attribute,url);
    }
  }
  if(bundle.preview){
    if(!parsed.querySelector('base[href]')){
      const base=parsed.createElement('base');base.setAttribute('href',bundle.preview.baseUrl);parsed.head.prepend(base);
    }
    const report=parsed.createElement('script');report.textContent=bundle.preview.script;parsed.body.append(report);
  }
  html='<!doctype html>'+parsed.documentElement.outerHTML;
}
document.open();document.write(html);document.close();
})()</script>`
}
