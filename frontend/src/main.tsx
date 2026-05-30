import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import './styles.css';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Root element #root was not found.');
}

const rootHost = globalThis as typeof globalThis & {
  __canvasMaterialRoot?: ReturnType<typeof createRoot>;
};
const root = rootHost.__canvasMaterialRoot ?? createRoot(rootElement);
rootHost.__canvasMaterialRoot = root;

root.render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);
