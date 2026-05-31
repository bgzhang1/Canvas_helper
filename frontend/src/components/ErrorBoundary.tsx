import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode };
type State = { hasError: boolean; message: string };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' };

  static getDerivedStateFromError(error: unknown): State {
    return { hasError: true, message: error instanceof Error ? error.message : String(error) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('UI error boundary caught an error', error, info);
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div style={{ padding: 32, fontFamily: 'system-ui, sans-serif', color: '#111' }}>
        <h1 style={{ fontSize: 18, margin: '0 0 8px' }}>页面出现错误 / Something went wrong</h1>
        <p style={{ color: '#666', fontSize: 14, margin: '0 0 16px', wordBreak: 'break-word' }}>{this.state.message}</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          style={{ padding: '8px 16px', cursor: 'pointer', border: '1px solid #111', background: '#fff', borderRadius: 6 }}
        >
          重新加载 / Reload
        </button>
      </div>
    );
  }
}
