import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 중에는 API(python -m api.main, :8000)로 프록시한다.
const api = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/domains": api, "/runs": api } },
  build: { chunkSizeWarningLimit: 800 }, // recharts 포함, 로컬 도구라 분할하지 않음
});
