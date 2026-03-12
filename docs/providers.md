# Providers

## Source Acquisition

- Base interface: [src/yt2slides/source_acquisition/base.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/source_acquisition/base.py)
- `yt-dlp` adapter: [src/yt2slides/source_acquisition/yt_dlp_adapter.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/source_acquisition/yt_dlp_adapter.py)
- Local media adapter: [src/yt2slides/source_acquisition/local_media_adapter.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/source_acquisition/local_media_adapter.py)

## Transcription

- Base interface: [src/yt2slides/transcription/base.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/transcription/base.py)
- Whisper CLI provider: [src/yt2slides/transcription/whisper_cli.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/transcription/whisper_cli.py)
- Generic command provider: [src/yt2slides/transcription/command_provider.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/transcription/command_provider.py)

## NotebookLM

- Base interface: [src/yt2slides/notebooklm/base.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/notebooklm/base.py)
- CLI adapter: [src/yt2slides/notebooklm/notebooklm_mcp_cli_adapter.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/notebooklm/notebooklm_mcp_cli_adapter.py)

All calls to `notebooklm-mcp-cli` are isolated in the CLI adapter. The stage persists raw requests, raw responses, normalized outputs, and logs so the integration can be retried or replaced later.

## TTS / Voice Cloning

- Base interface: [src/yt2slides/tts/base.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/tts/base.py)
- Generic command provider: [src/yt2slides/tts/command_provider.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/tts/command_provider.py)
- Manual placeholder provider: [src/yt2slides/tts/manual_provider.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/tts/manual_provider.py)

The command provider expects a template that can consume:

- `{text}`
- `{input_text}`
- `{output_audio}`
- `{reference_audio}`
- `{voice_id}`
- `{slide_number}`

## Rendering

- Slides: [src/yt2slides/rendering/slides.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/rendering/slides.py)
- Video/media: [src/yt2slides/rendering/video.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/rendering/video.py)
