# Third-party notices

Motorcycle Man's own code is under the MIT License (see [LICENSE](LICENSE)). It uses the following
third-party software. The built `Motorcycle Man.exe` includes the packages in the first table.

## Included in Motorcycle Man.exe

| Package | License | Source |
|---|---|---|
| Qt 6 and PySide6 / Shiboken6 | GNU LGPL v3 | https://www.qt.io, https://pypi.org/project/PySide6/ |
| Pillow | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| comtypes | MIT | https://github.com/enthought/comtypes |
| qrcode | BSD 3-Clause | https://github.com/lincolnloop/python-qrcode |
| colorama (used by qrcode) | BSD 3-Clause | https://github.com/tartley/colorama |
| websockets | BSD 3-Clause | https://github.com/python-websockets/websockets |
| anthropic (Anthropic's Python SDK, for Claude) | MIT | https://github.com/anthropics/anthropic-sdk-python |
| pydantic, pydantic-core, annotated-types, typing-inspection | MIT | https://github.com/pydantic/pydantic |
| httpx2, httpcore2 | BSD 3-Clause | https://pypi.org/project/httpx2/ |
| anyio, h11, jiter, truststore, docstring-parser | MIT | https://pypi.org (each package's page) |
| sniffio | MIT or Apache 2.0 | https://github.com/python-trio/sniffio |
| idna | BSD 3-Clause | https://github.com/kjd/idna |
| opentelemetry-api | Apache 2.0 | https://github.com/open-telemetry/opentelemetry-python |
| typing-extensions | Python Software Foundation License | https://github.com/python/typing_extensions |
| Python | Python Software Foundation License | https://www.python.org |
| PyInstaller bootloader | GPL v2 with the bootloader exception (allows distributing built programs) | https://pyinstaller.org |

**About Qt and PySide6 (LGPL v3):** these libraries are used unmodified. You can get their source code from
the links above. To use your own or a modified Qt or PySide6 with Motorcycle Man, install it and run or
rebuild Motorcycle Man from this repository's source (see the README). The full LGPL v3 text is at
https://www.gnu.org/licenses/lgpl-3.0.html.

## Downloaded only when you turn on a feature (not included)

| Program | License | Downloaded from |
|---|---|---|
| cloudflared (the internet link) | Apache 2.0 | https://github.com/cloudflare/cloudflared |
| ngrok agent (a fixed internet address, if you set one up) | ngrok's terms of service | https://ngrok.com |

## Artwork and icons

The helmet, rider and app icons are drawn by Motorcycle Man's own code (`Windows/helmet_face.py`,
`Windows/bike_rider.py`, `Icons/make_icons.py`). The engine sounds are synthesized in
`Windows/engine_sounds.py`. Some of the phone page's line icons follow the style and shapes of Feather Icons
(MIT License, Copyright (c) 2013-2023 Cole Bemis, https://feathericons.com).
