@echo off
echo Starting llama.cpp server with Vulkan GPU (RX 580)...
echo Model: Qwen 2.5 7B Instruct Q4_K_M
echo.

C:\Users\james\Tools\llama-cpp\llama-server.exe ^
  -m C:\Users\james\Models\qwen2.5-7b-instruct-q4_k_m.gguf ^
  -ngl 99 ^
  --device Vulkan0 ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --ctx-size 8192 ^
  --threads 2 ^
  --parallel 1

pause
