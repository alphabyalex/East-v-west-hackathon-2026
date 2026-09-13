import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { Landing } from './Landing';
import './styles.css';

const isApp = window.location.pathname.replace(/\/+$/, '') === '/app';
if (!isApp) document.title = 'Headroom — Price the flexible interconnection risk';

createRoot(document.getElementById('root')!).render(
  <StrictMode>{isApp ? <App /> : <Landing />}</StrictMode>,
);
