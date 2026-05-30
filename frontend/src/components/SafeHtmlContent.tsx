import { useMemo } from 'react';

const BLOCKED_TAGS = [
  'script',
  'style',
  'iframe',
  'object',
  'embed',
  'form',
  'input',
  'button',
  'textarea',
  'select',
  'link',
  'meta'
];

const URL_ATTRIBUTES = new Set(['href', 'src', 'xlink:href']);
const ALLOWED_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:']);

export function SafeHtmlContent({ html, emptyText }: { html: string | null | undefined; emptyText?: string }) {
  const safeHtml = useMemo(() => sanitizeHtml(html), [html]);
  if (!safeHtml) {
    return emptyText ? <p className="text-sm text-gray-500">{emptyText}</p> : null;
  }
  return <div className="safe-html-content text-sm leading-relaxed" dangerouslySetInnerHTML={{ __html: safeHtml }} />;
}

function sanitizeHtml(value: string | null | undefined) {
  if (!value) return '';
  const doc = new DOMParser().parseFromString(value, 'text/html');
  doc.querySelectorAll(BLOCKED_TAGS.join(',')).forEach((element) => element.remove());
  doc.querySelectorAll('*').forEach((element) => {
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith('on') || name === 'style') {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (URL_ATTRIBUTES.has(name) && !isSafeUrl(attribute.value)) {
        element.removeAttribute(attribute.name);
      }
    }
  });
  doc.querySelectorAll<HTMLAnchorElement>('a[href]').forEach((anchor) => {
    anchor.setAttribute('target', '_blank');
    anchor.setAttribute('rel', 'noopener noreferrer');
  });
  return doc.body.innerHTML.trim();
}

function isSafeUrl(value: string) {
  try {
    const url = new URL(value, window.location.origin);
    return ALLOWED_PROTOCOLS.has(url.protocol) || url.origin === window.location.origin;
  } catch {
    return false;
  }
}
