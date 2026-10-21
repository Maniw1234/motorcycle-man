# Motorcycle Man

A cute desktop buddy for Windows that you control from your phone. A little helmeted rider lives by your
mouse, and you tell it what to do ("open Gmail", "pause the music", "find my resume") by talking or typing on
your phone, in a chat app, or on the PC itself. An AI of your choice, with your own key, understands what
you ask and works your screen: Google Gemini (free), Anthropic Claude, OpenAI's ChatGPT, or any service that
speaks OpenAI's API (OpenRouter, Groq, Mistral, DeepSeek, xAI, or a model on your PC through Ollama or LM
Studio).

## What it can do

- **Talk or type commands** from the Android app, any phone browser, or a PC shortcut (Ctrl+Alt+M, or "Hey
  Motorcycle Man").
- **See and use your PC from the phone:** live screen view with zoom and sound, touchpad and keyboard,
  slides remote, game pad, open windows, music and volume per app, quick settings (dark mode, Wi-Fi,
  Bluetooth, Do not disturb).
- **Files and clipboard:** send files both ways, browse folders, find files, clipboard sync, and copy text
  from the screen.
- **Phone and PC together:** your phone's notifications on the PC, with replies; find my phone; the phone's
  battery on the PC.
- **Useful extras:** reminders, routines, research reports, a morning brief, PC health alerts, power timers
  and Wake-on-LAN, screen time, focus mode, shared lists, and a guard that tells you when someone uses your
  PC.
- **Other ways in, if you set them up:** Telegram, Discord, email, WhatsApp/Instagram (Meta's API), Alexa,
  Siri Shortcuts, and text messages to your phone.

## How it works

The Windows app runs a small web server on your PC. Your phone connects to it over your home Wi-Fi with a
secret link (scan the QR code, or let the Android app find the PC and approve it on the PC). Away from home,
you can turn on an internet link through Cloudflare's free tunnel. Commands that need to look at the screen
send a screenshot to your AI, which answers with the clicks and keys to do it.

## Requirements

- Windows 10 or 11 (developed and tested on Windows 11 with Python 3.15).
- An AI key of your own, added in the window's **AI** row:
  - **Google Gemini:** free key from Google AI Studio. Google's Gemini API terms require users to be 18 or
    older.
  - **Anthropic Claude** or **OpenAI (ChatGPT):** a key from their developer consoles, paid by use on your
    account.
  - **Another AI:** any OpenAI-compatible service, with its address and a model that can see pictures. Ollama
    or LM Studio on your PC need no key and keep everything on the PC.

  Every AI service has its own terms (age limits, what it does with your data); read the one you pick.
- Optional: an Android phone (Android 7 or newer) for the app. Any phone browser works for the basics.

Voice commands: Gemini and ChatGPT write down what you say; with the other AIs, Windows' own speech
recognition does it on the PC (free, but less accurate).

## Running from source

```
cd Windows
pip install -r requirements.txt
python motorcycle_man.py
```

Pick your AI and paste your key in the window's **AI** row, then scan the QR code with your phone.

### Building Motorcycle Man.exe

```
cd Windows
python -m PyInstaller --noconfirm MotorcycleMan.spec
```

The exe lands in `Windows\dist`. If `Android\MotorcycleMan.apk` exists, it's bundled so phones can install
and update the app from the PC.

### Building the Android app

The build uses no Gradle. Put JDK 17 in `Android\tools\jdk17` and the Android SDK (build-tools 34.0.0 and
platform android-34) in `Android\tools\sdk`, then run `Android\build_apk.ps1` in PowerShell. The first build
makes a signing key in `Android\build\debug.keystore`. Keep that file private and back it up: phones only
accept updates signed with the same key.

## Automatic updates

Every push to `main` builds Motorcycle Man.exe and the Android app on GitHub
([.github/workflows/release.yml](.github/workflows/release.yml)) and publishes them as a release named
`build-<number>`, with a `SHA256SUMS.txt`. Motorcycle Man.exe looks for a newer release a minute after it
starts and every six hours. When there is one, Motorcycle Man tells you, and its menu by the clock gets
**Install update**. Nothing installs until you pick it: then it downloads the new exe, checks it against
`SHA256SUMS.txt`, closes, and comes back as the new version. Phones get the new Android app from the PC as
before, when its version number has gone up. A copy you built yourself counts as build 0, so it's offered the
newest release. To stop looking for updates, set `"check_updates": false` in
`%APPDATA%\Motorcycle Man\settings.json`. A fork changes `UPDATE_REPO` in `Windows\app_update.py`.

The GitHub build makes the Android app only when the repository has a secret named `ANDROID_KEYSTORE_B64`
holding your `Android\build\debug.keystore` in base64. It must be the key your phones' app was signed with,
or they won't accept the update. To add it, copy the key as base64 in PowerShell:

```
[Convert]::ToBase64String([IO.File]::ReadAllBytes("Android\build\debug.keystore")) | Set-Clipboard
```

then open the repository's **Settings > Secrets and variables > Actions > New repository secret**, name it
`ANDROID_KEYSTORE_B64`, paste, and press **Add secret**. Without it, releases still have the exe, but with no
Android app inside.

## Privacy

Read [PRIVACY.md](PRIVACY.md). In short: everything runs on your PC, there's no account and no server of
ours, but some features send data to services you choose. The big one is your AI: **commands that look at
the screen send screenshots to it.** On Gemini's free tier, Google may use what you send to improve its
products, with human reviewers. Don't use screen commands while private information is on screen, or use a
local model (Ollama, LM Studio) to keep everything on your PC.

## Security

Read [SECURITY.md](SECURITY.md). Anyone who has your phone link can control your PC, so treat it like a
password. Only approve phones you own, and keep the internet link off when you don't need it.

## Responsible use

Use Motorcycle Man only on computers and phones you own, or that you have clear permission to control.
Installing it on someone else's device to watch or control it without their knowledge can be illegal.

## Not affiliated

Motorcycle Man is an independent project. It isn't affiliated with or endorsed by Google, Anthropic, OpenAI,
Microsoft, Cloudflare, Telegram, Discord, Meta, Amazon, Apple, ngrok or any other company it works with.
Gemini, Claude, ChatGPT, Windows, WhatsApp, Instagram, Alexa, Siri and the other names mentioned are
trademarks of their owners.

## License

MIT, see [LICENSE](LICENSE). Third-party parts and their licenses are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
