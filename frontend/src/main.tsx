import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, fontFamily: 'sans-serif', color: '#7d3a2a' }}>
          <h2 style={{ marginBottom: 8 }}>Đã xảy ra lỗi hiển thị</h2>
          <pre style={{ fontSize: 12, background: '#fff5f2', padding: 12, borderRadius: 8, overflowX: 'auto' }}>
            {String(this.state.error)}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            style={{ marginTop: 12, padding: '8px 20px', borderRadius: 8, background: '#b27454', color: '#fff', border: 'none', cursor: 'pointer' }}
          >
            Thử lại
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
