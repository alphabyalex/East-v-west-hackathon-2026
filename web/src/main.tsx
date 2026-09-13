import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { Landing } from './Landing';
import './styles.css';

function Router() {
  const [currentPath, setCurrentPath] = useState(() => {
    return window.location.pathname.replace(/\/+$/, '') || '/';
  });

  useEffect(() => {
    const handlePopState = () => {
      setCurrentPath(window.location.pathname.replace(/\/+$/, '') || '/');
    };

    const handleGlobalClick = (e: MouseEvent) => {
      const anchor = (e.target as HTMLElement).closest('a');
      if (anchor && anchor instanceof HTMLAnchorElement) {
        // Ignore modifier keys and non-left clicks
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
          return;
        }

        // Ignore external targets or target="_blank"
        if (anchor.target && anchor.target !== '_self') {
          return;
        }

        const url = new URL(anchor.href);
        if (url.origin === window.location.origin) {
          const path = url.pathname.replace(/\/+$/, '') || '/';
          if (path === '/' || path === '/app') {
            e.preventDefault();
            window.history.pushState(null, '', path);
            setCurrentPath(path);
            
            // Scroll to top on navigation
            window.scrollTo({ top: 0, behavior: 'instant' });
          }
        }
      }
    };

    window.addEventListener('popstate', handlePopState);
    document.addEventListener('click', handleGlobalClick);

    return () => {
      window.removeEventListener('popstate', handlePopState);
      document.removeEventListener('click', handleGlobalClick);
    };
  }, []);

  useEffect(() => {
    if (currentPath === '/app') {
      document.title = 'Fluxline — Flexible Connection Analysis';
    } else {
      document.title = 'Fluxline — Price the flexible interconnection risk';
    }
  }, [currentPath]);

  return currentPath === '/app' ? <App /> : <Landing />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Router />
  </StrictMode>,
);
