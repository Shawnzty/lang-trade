# Providers

## Source Acquisition

- Base interface: [src/source_acquisition/base.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/source_acquisition/base.py)
- `yt-dlp` adapter: [src/source_acquisition/yt_dlp_adapter.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/source_acquisition/yt_dlp_adapter.py)
- Local media adapter: [src/source_acquisition/local_media_adapter.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/source_acquisition/local_media_adapter.py)

## Transcription

- Base interface: [src/transcription/base.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/transcription/base.py)
- Whisper CLI provider: [src/transcription/whisper_cli.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/transcription/whisper_cli.py)
- Generic command provider: [src/transcription/command_provider.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/transcription/command_provider.py)

## NotebookLM

- Base interface: [src/notebooklm/base.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/notebooklm/base.py)
- CLI adapter: [src/notebooklm/notebooklm_mcp_cli_adapter.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/notebooklm/notebooklm_mcp_cli_adapter.py)

All calls to `notebooklm-mcp-cli` are isolated in the CLI adapter. The stage persists raw requests, raw responses, normalized outputs, and logs so the integration can be retried or replaced later.

## TTS / Voice Cloning

- Base interface: [src/tts/base.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/tts/base.py)
- Generic command provider: [src/tts/command_provider.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/tts/command_provider.py)
- Fish Audio provider: [src/tts/fish_audio_provider.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/tts/fish_audio_provider.py)
- ElevenLabs provider: [src/tts/elevenlabs_provider.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/tts/elevenlabs_provider.py)
- Manual placeholder provider: [src/tts/manual_provider.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/tts/manual_provider.py)

The command provider expects a template that can consume:

- `{text}`
- `{input_text}`
- `{output_audio}`
- `{reference_audio}`
- `{voice_id}`
- `{slide_number}`

The Fish Audio and ElevenLabs adapters both create a voice clone from `tts.reference_audio_path` when `tts.voice_id` is empty. If you already have a cloned voice/model id, set `tts.voice_id` to reuse it and skip recloning on later runs.

## Rendering

- Slides: [src/rendering/slides.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/rendering/slides.py)
- Video/media: [src/rendering/video.py](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/rendering/video.py)
