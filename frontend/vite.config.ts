import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  build: {
    rollupOptions: {
      input: resolve(__dirname, 'app.html'),
      output: {
        // Giữ nguyên tên các module chunk để tránh Terser TDZ bug
        manualChunks: undefined,
      },
    },
    // Dùng esbuild thay Terser: nhanh hơn, không có TDZ bug với React components
    minify: 'esbuild',
    target: 'es2020',
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
