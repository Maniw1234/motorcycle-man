# Privacy

Motorcycle Man runs on your own PC and phone. There's no account, no analytics, and no server run by this
project. Data leaves your devices only for the features below, and only to the services named.

## What goes where

| Feature | Where the data goes | What is sent |
|---|---|---|
| Commands that look at the screen, research | The AI you chose: Google (Gemini), Anthropic (Claude), OpenAI (ChatGPT), or the service you set up; nothing leaves the PC with Ollama or LM Studio on it | Your command, a screenshot of your screen, a list of on-screen controls, recent requests, and for research the search results and the text of the top pages |
| Voice commands | Gemini or ChatGPT (with other AIs, Windows' own speech recognition on the PC) | The recording |
| Research | Bing, and the web pages found | Your search words; the pages are fetched from your PC (only pages on the public internet) |
| Morning brief weather | Open-Meteo | The town you set |
| Internet link (off until you turn it on) | Cloudflare, or ngrok if you set up a fixed address | Everything the phone and PC send each other while you're away (encrypted to Cloudflare/ngrok, which pass it on) |
| Telegram, Discord, email, WhatsApp/Instagram, Alexa (each off until you link it) | That service | Your messages to the bot, its answers, and screenshots when you ask for them or the guard sends one |
| Phone alerts on the PC (off until you turn it on) | Your PC only (over your Wi-Fi, or the internet link when it's on) | The phone's notification texts |
| Text-message commands (off until you turn it on) | Your mobile carrier | The texts and the PC's answers |
| Updates (Motorcycle Man.exe; `"check_updates": false` in settings.json turns them off) | GitHub | A request for the newest release a minute after it starts and every six hours, and the download when you install one; GitHub sees your IP address, nothing else about you |

**About Gemini's free tier:** Google's terms say that for free (unpaid) use, Google may use what you send,
and Gemini's answers, to improve its products, and human reviewers may read it. Paid AI services have their
own rules about keeping and using data; read the terms of the one you pick. Whatever the AI, don't run screen
commands while passwords, private messages or other sensitive things are showing.

## What is kept on your PC

In `%APPDATA%\Motorcycle Man`:

- `keys.json`: your AI keys and the keys of bots you link, stored as plain text, readable by programs
  running as your Windows user.
- `settings.json`: your choices, including which AI and model you use.
- `phone.json`: the phone link's secret.
- `motorcycle_man.log`: a log of commands, the AI's plans and what was done (up to about 3 MB), to trace
  problems. It can contain text from your screen and your commands.
- `history.json`, reminders, routines, lists, screen time and settings.

Deleting that folder removes everything. The Android app keeps its settings and the PC's link in its own app
storage.

## Permissions on Android

Microphone (speech), notifications, notification access (only for Phone alerts on the PC), SMS (only for
text-message commands), Bluetooth (only for the Bluetooth link), and installing updates of itself from your
PC. Each is asked for when you turn the feature on.
