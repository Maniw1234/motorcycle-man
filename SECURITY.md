# Security

## Reporting a problem

Please report security problems privately through GitHub's "Report a vulnerability" button on this
repository (Security tab), not in a public issue. Include what you found and how to reproduce it.

## How Motorcycle Man protects your PC

- **The phone link's secret.** Every request from a phone carries a random 128-bit secret, checked in
  constant time. Without it, the PC answers nothing but the page for connecting. Anyone who has your link
  can control your PC, so don't share it.
- **Pairing needs you.** A new phone gets the link only when you press Allow on the PC (after checking that
  the code matches your phone), or type the PC's six-digit code on the phone. Wrong codes lock pairing for a
  while, and after a refusal phones wait before asking again. A request that comes over the internet link
  is marked as such on the PC.
- **Web pages can't reach it.** The PC lets programs on the PC in without pairing only when Windows confirms
  the connection belongs to a program run by the same Windows user, and requests passed on by proxies or
  tunnels never count as local.
- **One copy, one port.** The phone port is opened exclusively, so another program can't share it.
- **Chat links** pair with a one-time code; guests' risky requests wait for the owner. Email commands must
  pass the mail service's DKIM/SPF checks; Meta deliveries must carry a valid signature; the Alexa skill
  uses a secret address.
- **The Android app** asks before following a link to a PC it has never connected to, sends Phone alerts
  only to the PC they were turned on for, and locks text commands after repeated wrong PINs.
- **Files from the phone** get safe Windows names and never overwrite anything.
- **Downloads** of Cloudflare's tunnel program (and ngrok's, if you set one up) are checked for their makers' digital
  signatures.

## Things to know

- **The AI acts on what it sees.** Your AI decides clicks and keys from screenshots, so text on the screen
  (a web page or an email) could try to trick it. Sending, deleting and similar actions wait for your yes,
  but watch what it does and stop it if something looks wrong.
- **Your AI keys** are yours: each user adds their own, and none come with Motorcycle Man. An
  OpenAI-compatible service must have an https:// address unless it runs on your PC or home network, so the
  key never crosses the internet unencrypted. Research only fetches pages on the public internet, never your
  PC, router or other devices at home.
- **Wi-Fi is plain HTTP.** On your home Wi-Fi, the phone and PC talk over HTTP. Don't use the Wi-Fi link on
  public or shared networks; use the internet link (HTTPS through Cloudflare) instead.
- **Keys are stored as plain text** in `%APPDATA%\Motorcycle Man\keys.json`, like most desktop apps.
  Programs running as your Windows user can read them.
- **Phones on your Wi-Fi can look for the PC.** The Android app finds PCs on the network by itself. A
  device pretending to be Motorcycle Man on your network could ask to connect; the app only joins after the
  "PC" accepts it, and Phone alerts stay with the PC you turned them on for.
