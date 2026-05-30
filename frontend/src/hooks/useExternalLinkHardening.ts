import { useEffect } from 'react';

/**
 * Forces every anchor in the document to open in a new tab with
 * rel="noopener noreferrer", both on initial mount and for links added later
 * (caught via capture-phase click/auxclick listeners).
 */
export function useExternalLinkHardening() {
  useEffect(() => {
    function markLink(link: HTMLAnchorElement) {
      link.target = '_blank';
      const rel = new Set(link.rel.split(/\s+/).filter(Boolean));
      rel.add('noopener');
      rel.add('noreferrer');
      link.rel = Array.from(rel).join(' ');
    }

    function markLinks(root: ParentNode) {
      root.querySelectorAll<HTMLAnchorElement>('a[href]').forEach(markLink);
    }

    function handleLinkClick(event: MouseEvent) {
      const target = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href]') : null;
      if (target) markLink(target);
    }

    markLinks(document);
    document.addEventListener('click', handleLinkClick, true);
    document.addEventListener('auxclick', handleLinkClick, true);
    return () => {
      document.removeEventListener('click', handleLinkClick, true);
      document.removeEventListener('auxclick', handleLinkClick, true);
    };
  }, []);
}
