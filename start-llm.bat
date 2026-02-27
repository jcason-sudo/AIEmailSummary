@echo off
echo Starting llama.cpp server with Vulkan GPU (RX 580)...
echo Model: Llama 3.1 8B Instruct Q4_K_M
echo.

C:\Users\james\Tools\llama-cpp\llama-server.exe ^
  -m C:\Users\james\Models\llama-3.1-8b-instruct-q4_k_m.gguf ^
  -ngl 99 ^
  --device Vulkan0 ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --ctx-size 4096 ^
  --threads 2 ^
  --parallel 1

pause
